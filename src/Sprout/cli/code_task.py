"""CLI preparation dispatcher for local Coding Tasks."""
import json
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

import questionary
import typer


def model_request_budget(value):
    """A finite, whole-Task allowance, confirmed before any Task is created."""
    limit = int(value)
    if not 1 <= limit <= 128:
        raise ValueError("Task model request budget must be between 1 and 128")
    return limit


async def prepare(
    runtime, workspace_id, *, message, session, principal, reproduced_failure=None,
    spec_probe=False, reproduced_spec=None,
):
    from Sprout.cli.filesystem_preparation import discover_plan, preflight, preparation_record
    from Sprout.cli.filesystem_task import prepare_filesystem
    from Sprout.execution.acceptance_producer import check_preparation, produce
    from Sprout.gateway.identity import Principal
    from Sprout.llm.orchestrator import ModelOrchestrationError
    from Sprout.orchestration.terminal import LocalOrchestratorBackend
    from Sprout.runtime.isolated_test_run import execution_request
    from Sprout.security.local_diagnostics import report
    from Sprout.task.models import DelegationScope, TaskBudget, TaskExecutionMode
    from Sprout.workspace.models import WorkspaceKind

    if (not isinstance(principal, Principal) or not principal.authenticated
            or principal.source != "cli"):
        raise PermissionError("cli_code_subject_unavailable")
    owned = await runtime._sessions.require_owner(session.id, principal)
    if runtime._sessions.frozen_principal(owned) != principal:
        raise PermissionError("cli_code_subject_unavailable")
    bound = runtime.session_workspace_id(owned)
    if bound and workspace_id and bound != workspace_id:
        raise PermissionError("cli_code_subject_unavailable")
    workspace_id = bound or workspace_id
    workspace = await runtime.get_workspace(workspace_id) if workspace_id else None
    if workspace is None or not workspace.root.is_dir():
        raise PermissionError("cli_code_workspace_unavailable")
    if not bound:
        raise PermissionError("cli_code_subject_unavailable")
    if execution_request(message.content):
        return await prepare_filesystem(runtime, workspace, message, owned, principal,
                                        test_only=True)

    if workspace.kind is WorkspaceKind.LOCAL_DIRECTORY:
        if (not isinstance(runtime._orchestrator_backend, LocalOrchestratorBackend)
                or (workspace.root / ".git").exists()):
            raise PermissionError("filesystem_experiment_local_foreground_required")
    elif workspace.kind is not WorkspaceKind.GIT_REPOSITORY:
        raise PermissionError("cli_code_workspace_unavailable")

    if workspace.kind is WorkspaceKind.LOCAL_DIRECTORY:
        return await prepare_filesystem(
            runtime, workspace, message, owned, principal, reproduced_failure=reproduced_failure,
            test_only=spec_probe, spec_probe=spec_probe, reproduced_spec=reproduced_spec,
        )
    if spec_probe or reproduced_spec is not None:
        raise PermissionError("spec_probe_filesystem_workspace_required")

    captured = []
    plan = await discover_plan(
        runtime, workspace, message, owned, principal, on_evidence=captured.extend,
        reproduced_failure=reproduced_failure,
    )
    report(runtime, "Acceptance", "generating for Git")
    try:
        bundle = await produce(
            runtime.models.default(), message.content, plan, captured, require_git_coverage=True,
        )
        prepared = preparation_record(plan, adapter="git", state="awaiting_internal_acceptance")
        prepared["write_evidence"] = bundle["write_evidence"]
        check_preparation(bundle, message.content, prepared)
    except ValueError as exc:
        raise PermissionError(f"git_acceptance_failed: {exc}") from exc
    except ModelOrchestrationError as exc:
        raise PermissionError("git_acceptance_model_unavailable") from exc
    report(runtime, "Acceptance", f"frozen: {bundle['bundle_sha256']}")
    current = await runtime._sessions.require_owner(owned.id, principal)
    if (runtime._sessions.frozen_principal(current) != principal
            or runtime.session_workspace_id(current) != workspace.id
            or await runtime.get_workspace(workspace.id) != workspace):
        raise PermissionError("cli_code_subject_changed")
    scope = DelegationScope(
        allowed_paths=tuple(dict.fromkeys((*plan["reads"], *plan["writes"]))),
        allowed_actions=frozenset({"tool.invoke", "file.read", "file.write",
                                   "process.run", "git.diff", "file.apply"}),
        issued_by=f"cli:{principal.user_id}",
    )
    typer.echo("[Planning] read: " + ", ".join(plan["reads"]))
    typer.echo("[Planning] write: " + ", ".join(plan["writes"]))
    for phase in plan["execution"]:
        typer.echo(f"[Planning] execute: {phase}")
    task = await runtime.create_task(
        workspace.id, message.content, actor=principal, source="cli",
        execution_mode=TaskExecutionMode.CODE, delegation_scope=scope,
        budget=TaskBudget(max_model_calls=24),
        metadata={
            "session_id": current.id, "required_paths": prepared["reads"],
            "cli_write_paths": prepared["writes"], "coding_preparation_v1": prepared,
            "git_acceptance_v1": bundle,
        },
    )
    try:
        task = await runtime.bind_task_acceptance(
            task.id, principal=principal, git_executable=shutil.which("git"),
        )
    except (OSError, ValueError) as exc:
        reason = (str(exc) if str(exc).startswith(("git_acceptance_", "host_acceptance_",
                                                   "acceptance_"))
                  else "git_acceptance_binding_unavailable")
        current = await runtime.get_task(task.id)
        if current != task:
            raise PermissionError("git_acceptance_subject_changed") from exc
        task = replace(task, metadata={**task.metadata, "git_acceptance_binding_error": reason})
        await runtime._metadata_or_raise().save_task(task)
        report(runtime, "Acceptance", f"blocked: {reason}")
    else:
        image = await preflight(runtime, workspace, current, principal)
        prepared = {**prepared, "state": "ready", "coding_image": image}
        task = replace(task, metadata={**task.metadata, "coding_preparation_v1": prepared,
                                       "require_acceptance_tests": True})
        await runtime._metadata_or_raise().save_task(task)
        runtime._cli_code_writes[task.id] = (principal, current.id, workspace.id,
                                             tuple(prepared["writes"]))
        report(runtime, "Acceptance", "v2 bound")
    return task


async def prepare_manual(runtime, workspace_id, *, message, session, principal):
    """Explicit host preparation for the existing frozen acceptance APIs."""
    from Sprout.strategy.acceptance import AcceptanceSpec
    from Sprout.strategy.execution import ExecutionPlanner
    from Sprout.task.models import DelegationScope
    from Sprout.workspace.authorized_content import relative_label

    owned = await runtime._sessions.require_owner(session.id, principal)
    if runtime._sessions.frozen_principal(owned) != principal:
        raise PermissionError("cli_code_subject_unavailable")
    bound = runtime.session_workspace_id(owned)
    if bound and workspace_id and bound != workspace_id:
        raise PermissionError("cli_code_subject_unavailable")
    workspace_id = bound or workspace_id
    workspace = await runtime.get_workspace(workspace_id) if workspace_id else None
    if workspace is None or not workspace.root.is_dir():
        raise PermissionError("cli_code_workspace_unavailable")
    prompts = (
        ("reads", "READ inputs (comma-separated Workspace-relative files):"),
        ("writes", "WRITE targets (comma-separated; only these files may change):"),
        ("spec", "Acceptance specification JSON path (Host requirement, not model output):"),
        ("test", "Frozen acceptance test path (Workspace-relative):"),
        ("demo", "Frozen acceptance Demo path (Workspace-relative):"),
        ("git", "Trusted Git executable absolute path:"),
        ("git_sha", "Trusted Git executable SHA256:"),
    )
    values = {}
    try:
        for key, prompt in prompts:
            value = await questionary.text(prompt).ask_async()
            if not value or not value.strip():
                return None
            values[key] = value.strip()
        calls = await questionary.text(
            "Task model request budget (Plan and all Agent retries; 1-128):",
            default="24").ask_async()
        if calls is None:
            return None
        model_calls = model_request_budget(calls or "24")
        reads = tuple(dict.fromkeys(relative_label(p.strip()) for p in values["reads"].split(",")))
        writes = tuple(dict.fromkeys(
            relative_label(p.strip()) for p in values["writes"].split(",")))
        test, demo = relative_label(values["test"]), relative_label(values["demo"])
        if not reads or not writes or not set((*writes, test, demo)).issubset(reads):
            raise ValueError("Write targets, frozen test and Demo must be explicit READ inputs")
        if test in writes or demo in writes:
            raise ValueError("Frozen acceptance inputs cannot be write targets")
        spec = AcceptanceSpec.from_payload(json.loads(Path(values["spec"]).read_text("utf-8")))
        git = Path(values["git"])
        if not git.is_absolute() or len(values["git_sha"]) != 64:
            raise ValueError("Absolute trusted Git path and exact SHA256 required")
        typer.echo(json.dumps({"workspace": str(workspace.root), "session_id": owned.id,
            "instruction": message.content, "read_paths": reads, "write_paths": writes,
            "acceptance": spec.to_payload(), "test": test, "demo": demo,
            "git_executable": str(git), "git_sha256": values["git_sha"],
            "max_model_calls": model_calls,
            "actions": ["tool.invoke", "file.read", "file.write", "process.run",
                        "git.diff", "file.apply"],
            "notice": "Preparation only. Execution and final Apply require separate approvals."
        }, ensure_ascii=False, indent=2))
        if not await questionary.confirm(
                "Confirm this exact CODE preparation?", default=False).ask_async():
            return None
    except (KeyboardInterrupt, EOFError):
        return None
    except (ValueError, OSError, TypeError) as exc:
        raise PermissionError(f"cli_code_preparation_invalid: {exc}") from exc
    current = await runtime._sessions.require_owner(owned.id, principal)
    if (runtime._sessions.frozen_principal(current) != principal
            or await runtime.get_workspace(workspace_id) != workspace
            or runtime.session_workspace_id(current) not in {"", workspace_id}):
        raise PermissionError("cli_code_subject_changed")
    if not runtime.session_workspace_id(current):
        await runtime._remember_session_workspace(current.id, workspace_id)
    scope = DelegationScope(allowed_paths=reads,
        allowed_actions=frozenset({"tool.invoke", "file.read", "file.write", "process.run",
                                  "git.diff", "file.apply"}),
        issued_by=principal.source + ":" + principal.user_id)
    task = await runtime.create_task_planned(workspace_id, message.content,
        actor=principal, source=principal.source, delegation_scope=scope,
        preparation_paths=reads)
    task = replace(task,
        instruction=ExecutionPlanner.bind_file_contract(
            task.instruction, reads=reads, writes=writes),
        budget=replace(task.budget, max_model_calls=model_calls),
        metadata={**task.metadata, "session_id": current.id,
                                  "cli_write_paths": list(writes)})
    await runtime._metadata_or_raise().save_task(task)
    task = await runtime.bind_task_acceptance(task.id, principal=principal,
        spec=spec.to_payload(), test_path=test, demo_path=demo)
    await runtime.configure_local_git_review(task.id, principal,
        executable=str(git), executable_sha256=values["git_sha"],
        export_parent=tempfile.mkdtemp(prefix="sprout-g1-"))
    # Negative host restriction, not a Scope grant or serialized capability.
    runtime._cli_code_writes[task.id] = (principal, current.id, workspace.id, writes)
    typer.echo(f"CODE Task: {task.id}")
    return task


def provider(runtime, workspace_id, live=None):
    async def provide(**request):
        if live is not None:
            live.stop()
        try:
            return await prepare(runtime, workspace_id, **request)
        finally:
            if live is not None:
                live.start()
    return provide
