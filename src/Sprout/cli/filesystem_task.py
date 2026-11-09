"""Explicit foreground admission for a local filesystem Coding Task."""

import hashlib
import json
import os
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import questionary
import typer

from Sprout.execution.docker_verification import safe_path, validate_image
from Sprout.orchestration.terminal import LocalOrchestratorBackend
from Sprout.runtime.filesystem_experiment import FilesystemExperiment
from Sprout.sandbox.filesystem import checked_label
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import DelegationScope, TaskExecutionMode


async def prepare_filesystem(runtime, workspace, message, owned, principal):
    if (not isinstance(runtime._orchestrator_backend, LocalOrchestratorBackend)
            or not principal.authenticated or principal.source != "cli"
            or runtime.session_workspace_id(owned) != workspace.id):
        raise PermissionError("filesystem_experiment_local_foreground_required")
    try:
        reads = await questionary.text("Experiment READ files (comma-separated relative paths):"
                                       ).ask_async()
        writes = await questionary.text("Experiment WRITE files (comma-separated relative paths):"
                                        ).ask_async()
        if not reads or not writes:
            return None
        overrides = runtime._file_broker._classify_overrides
        reads, writes = (tuple(dict.fromkeys(checked_label(
            item.strip(), workspace.root, overrides) for item in value.split(",")))
            for value in (reads, writes))
        if max(len(reads), len(writes)) > 64:
            raise ValueError("At most 64 explicit inputs and outputs are supported")
        image = os.getenv("SPROUT_FILESYSTEM_CODING_IMAGE", "").strip()
        if image:
            validate_image(image)
        frozen = None
        raw_spec = os.getenv("SPROUT_FILESYSTEM_VERIFICATION_SPEC", "")
        if raw_spec:
            if not image or len(raw_spec) > 24 * 1024:
                raise ValueError("Pinned image and bounded Host verification spec required")
            bundle = json.loads(raw_spec)
            if not isinstance(bundle, dict) or set(bundle) != {"acceptance", "regression"}:
                raise ValueError("Host acceptance and regression specifications required")
            specs = {name: AcceptanceSpec.from_payload(spec).to_payload()
                     for name, spec in bundle.items()}
            frozen = {
                "specs": specs,
                "source": "host-env:SPROUT_FILESYSTEM_VERIFICATION_SPEC",
                "sha256": hashlib.sha256(json.dumps(specs, sort_keys=True).encode()).hexdigest(),
                "frozen_at": datetime.now(UTC).isoformat(),
            }
        confirmation = (
            f"Host verification spec {frozen['sha256']}. "
            "Passing verification enables exact Apply to the original workspace. "
            if frozen else ""
        )
        if not await questionary.confirm(
                f"Experiment only. Read {reads}; edit {writes}; "
                "output paths are checked in the original for conflicts; "
                f"Docker image {image or 'not configured'}. {confirmation}Confirm?",
                default=False).ask_async():
            return None
    except (EOFError, KeyboardInterrupt):
        return None
    except (OSError, ValueError) as exc:
        raise PermissionError("filesystem_experiment_selection_invalid") from exc

    current = await runtime._sessions.require_owner(owned.id, principal)
    if (runtime._sessions.frozen_principal(current) != principal
            or runtime.session_workspace_id(current) != workspace.id
            or await runtime.get_workspace(workspace.id) != workspace):
        raise PermissionError("filesystem_experiment_subject_changed")
    # The source export has its own exact file list, while the ProcessBroker
    # scope admits only this workspace as the host working directory.
    actions = {"tool.invoke", "file.read", "file.write"}
    if image:
        actions.add("process.run")
    if frozen:
        actions.add("file.apply")
    scope = DelegationScope(allowed_paths=(".",), allowed_actions=frozenset(actions),
                            issued_by="cli:" + principal.user_id)
    evidence_parent = safe_path(Path(tempfile.gettempdir()))
    if evidence_parent.is_relative_to(workspace.root):
        raise PermissionError("filesystem_experiment_evidence_overlaps_workspace")
    task = await runtime.create_task(
        workspace.id, message.content, actor=principal, source="cli",
        delegation_scope=scope, execution_mode=TaskExecutionMode.CODE,
        _filesystem_foreground=True,
        metadata={"session_id": owned.id, "filesystem_experiment": True,
                  "cli_write_paths": list(writes)},
    )
    task = replace(task, budget=replace(task.budget, max_model_calls=24))
    await runtime._metadata_or_raise().save_task(task)
    runtime._cli_code_writes[task.id] = (principal, owned.id, workspace.id, writes)
    evidence = evidence_parent / ("filesystem-" + task.id)
    evidence.mkdir(exist_ok=False)
    experiment = FilesystemExperiment(
        runtime, task, workspace, reads, image, evidence, verification=frozen)
    async def confirm_apply(arguments, review_text):
        if review_text:
            typer.echo(review_text)
        lines = "\n".join(
            f"{item['path']}: {item['before'] or 'absent'} -> {item['after']}"
            for item in arguments["files"]
        )
        return await questionary.confirm(
            f"Apply transaction {arguments['transaction']} to the original workspace?\n{lines}",
            default=False,
        ).ask_async() is True

    async def confirm_tool(record):
        return await questionary.confirm(
            f"Allow {record.tool} for this task? Approval {record.id}",
            default=False,
        ).ask_async() is True

    experiment.confirm_apply = confirm_apply
    experiment.confirm_tool = confirm_tool
    runtime._sandbox_lifecycle.filesystem[task.id] = experiment
    return task
