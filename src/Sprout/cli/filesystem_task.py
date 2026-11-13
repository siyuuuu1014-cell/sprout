"""Explicit foreground admission for a local filesystem Coding Task."""

import hashlib
import json
import os
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import typer

from Sprout.execution.docker_verification import safe_path
from Sprout.orchestration.terminal import LocalOrchestratorBackend
from Sprout.runtime.filesystem_experiment import FilesystemExperiment
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import DelegationScope, TaskExecutionMode


def _unavailable_language(root):
    """Identify language-specific projects before resolving a Python image."""
    if (root / "pyproject.toml").is_file():
        return None
    if (root / "go.mod").is_file():
        return "Go"
    if (root / "package.json").is_file():
        return "Node/TypeScript"
    return None


async def prepare_filesystem(
    runtime, workspace, message, owned, principal, *, test_only=False, reproduced_failure=None,
    spec_probe=False, reproduced_spec=None,
):
    if (not isinstance(runtime._orchestrator_backend, LocalOrchestratorBackend)
            or not principal.authenticated or principal.source != "cli"
            or runtime.session_workspace_id(owned) != workspace.id):
        raise PermissionError("filesystem_experiment_local_foreground_required")
    try:
        from Sprout.cli.filesystem_preparation import discover_plan, preflight, preparation_record

        language = _unavailable_language(workspace.root)
        if language:
            raise PermissionError(f"filesystem_coding_language_unsupported: {language}")
        image = await preflight(runtime, workspace, owned, principal)
        raw_spec = os.getenv("SPROUT_FILESYSTEM_VERIFICATION_SPEC", "")
        captured = []
        plan = await discover_plan(
            runtime, workspace, message, owned, principal,
            on_evidence=captured.extend if (
                spec_probe or reproduced_spec is not None or not test_only and not raw_spec
            ) else None,
            **({"reproduced_failure": reproduced_failure} if reproduced_failure else {}),
            **({"spec_probe": True} if spec_probe else {}),
            **({"reproduced_spec": reproduced_spec} if reproduced_spec is not None else {}),
        )
        reads, writes = plan["reads"], plan["writes"]
        typer.echo("[Planning] read: " + ", ".join(reads))
        operations = [{"path": path, "operation": "update" if path in reads else "create"}
                      for path in (() if spec_probe else writes)]
        for operation in operations:
            typer.echo(f"[Planning] {operation['operation']}: {operation['path']}")
        for execution in plan.get("execution", ()):
            typer.echo(f"[Planning] execute: {execution}")
        tests = tuple(path for path in reads if path.endswith('.py') and (
            Path(path).name.startswith('test_') or Path(path).name.endswith('_test.py')))
        if test_only and not tests:
            raise PermissionError("project_tests_not_found")
        from Sprout.security.local_diagnostics import current

        test_run = None
        if (not test_only and "run_tests" in plan.get("execution", ())
                and current(runtime) is not None):
            targets = (*tests, *(path for path in writes if Path(path).name.startswith("test_")
                                 or Path(path).name.endswith("_test.py")))
            if not image or not targets:
                raise PermissionError("filesystem_test_plan_unavailable")
            framework = "unittest" if "unittest" in message.content.casefold() else "pytest"
            test_run = {"argv": ["python", "-m", framework, "-q"], "cwd": ".",
                        "workspace_id": workspace.id, "image": image, "repair_attempts": 1}
        frozen = None
        automatic_bundle = None
        probe_bundle = None
        if spec_probe or reproduced_spec is not None:
            from Sprout.execution.acceptance_producer import (
                bind_probe_plan,
                check_preparation,
                produce,
            )
            from Sprout.llm.orchestrator import ModelOrchestrationError
            from Sprout.security.local_diagnostics import report

            try:
                if spec_probe:
                    report(runtime, "Acceptance", "generating read-only specification probe")
                    probe_bundle = await produce(
                        runtime.models.default(), message.content, plan, captured, spec_probe=True,
                    )
                    check_preparation(probe_bundle, message.content, plan)
                else:
                    automatic_bundle = bind_probe_plan(
                        *reproduced_spec, requirement=message.content, plan=plan,
                    )
                    check_preparation(automatic_bundle, message.content, plan)
            except (ValueError, ModelOrchestrationError) as exc:
                raise PermissionError(f"filesystem_spec_probe_unavailable: {exc}") from exc
        elif raw_spec and not test_only:
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
        elif not test_only:
            from Sprout.execution.acceptance_producer import check_preparation, produce
            from Sprout.llm.orchestrator import ModelOrchestrationError
            from Sprout.security.local_diagnostics import report

            if not image:
                raise PermissionError("acceptance_docker_image_unavailable")
            report(runtime, "Acceptance", "generating")
            try:
                automatic_bundle = await produce(
                    runtime.models.default(), message.content, plan, captured,
                )
                check_preparation(automatic_bundle, message.content, plan)
            except ValueError as exc:
                raise PermissionError(f"filesystem_acceptance_failed: {exc}") from exc
            except ModelOrchestrationError as exc:
                raise PermissionError("filesystem_acceptance_model_unavailable") from exc
            report(runtime, "Acceptance", f"frozen: {automatic_bundle['bundle_sha256']}")
    except (EOFError, KeyboardInterrupt):
        return None
    except PermissionError:
        raise
    except (OSError, ValueError) as exc:
        raise PermissionError("filesystem_experiment_selection_invalid") from exc

    current = await runtime._sessions.require_owner(owned.id, principal)
    if (runtime._sessions.frozen_principal(current) != principal
            or runtime.session_workspace_id(current) != workspace.id
            or await runtime.get_workspace(workspace.id) != workspace):
        raise PermissionError("filesystem_experiment_subject_changed")
    # The source export has its own exact file list, while the ProcessBroker
    # scope admits only this workspace as the host working directory.
    actions = {"tool.invoke", "file.read"}
    if not test_only:
        actions.add("file.write")
    if image:
        actions.add("process.run")
    if frozen or automatic_bundle:
        actions.add("file.apply")
    scope = DelegationScope(allowed_paths=(".",), allowed_actions=frozenset(actions),
                            issued_by="cli:" + principal.user_id)
    evidence_parent = safe_path(Path(tempfile.gettempdir()))
    if evidence_parent.is_relative_to(workspace.root):
        raise PermissionError("filesystem_experiment_evidence_overlaps_workspace")
    prepared = preparation_record(plan, adapter="filesystem", state="ready")
    if spec_probe:
        writes = ()
        prepared = {**prepared, "writes": []}
    if automatic_bundle:
        prepared["write_evidence"] = automatic_bundle["write_evidence"]
    task = await runtime.create_task(
        workspace.id, message.content, actor=principal, source="cli",
        delegation_scope=scope, execution_mode=TaskExecutionMode.CODE,
        _filesystem_foreground=True,
        metadata={"session_id": owned.id, "filesystem_experiment": True,
                  "cli_write_paths": list(writes),
                  **({"filesystem_spec_probe_v1": probe_bundle} if probe_bundle else {}),
                  **({"filesystem_acceptance_v1": automatic_bundle} if automatic_bundle else {}),
                  "coding_preparation_v1": prepared,
                  "filesystem_plan_v1": {"reads": list(reads), "operations": operations,
                      "execution": list(plan.get("execution", ())),
                      "actions": list(plan.get("actions", ())),
                      **({"host_test": test_run} if test_run is not None else {})}},
    )
    task = replace(task, budget=replace(task.budget, max_model_calls=24))
    await runtime._metadata_or_raise().save_task(task)
    runtime._cli_code_writes[task.id] = (principal, owned.id, workspace.id, writes)
    evidence = evidence_parent / ("filesystem-" + task.id)
    evidence.mkdir(exist_ok=False)
    from Sprout.runtime.isolated_test_run import FilesystemTestRun, run_prepared

    experiment = (FilesystemTestRun if test_only else FilesystemExperiment)(
        runtime, task, workspace, reads, image, evidence, verification=frozen)
    if test_only:
        experiment.test_files = tests
        experiment.spec_probe = probe_bundle

    # This is a live Host-owned experiment, never a persisted or model option.
    experiment.automatic = True
    if not test_only and not automatic_bundle and "run_tests" in plan.get("execution", ()):
        async def post_apply_tests():
            fresh = await runtime._sessions.require_owner(owned.id, principal)
            test_message = replace(
                message, content="只运行当前项目已有的单元测试，不修改任何文件")
            test_task = await prepare_filesystem(
                runtime, workspace, test_message, fresh, principal, test_only=True)
            return await run_prepared(runtime, test_task, fresh, principal)

        experiment.post_apply_tests = post_apply_tests
    runtime._sandbox_lifecycle.filesystem[task.id] = experiment
    return task
