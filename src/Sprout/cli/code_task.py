"""Interactive adapter for the existing planned Task / frozen acceptance / G1 APIs."""
import json
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


async def prepare(runtime, workspace_id, *, message, session, principal):
    from Sprout.strategy.acceptance import AcceptanceSpec
    from Sprout.strategy.execution import ExecutionPlanner
    from Sprout.task.models import DelegationScope
    from Sprout.workspace.authorized_content import relative_label

    workspace = await runtime.get_workspace(workspace_id)
    if workspace is None:
        raise PermissionError("cli_code_workspace_unavailable")
    owned = await runtime._sessions.require_owner(session.id, principal)
    if runtime._sessions.frozen_principal(owned) != principal:
        raise PermissionError("cli_code_subject_unavailable")
    from Sprout.runtime.isolated_test_run import execution_request

    if execution_request(message.content):
        from Sprout.cli.filesystem_task import prepare_filesystem

        return await prepare_filesystem(runtime, workspace, message, owned, principal,
                                        test_only=True)
    from Sprout.workspace.models import WorkspaceKind

    if workspace.kind is WorkspaceKind.LOCAL_DIRECTORY and not (workspace.root / ".git").exists():
        from Sprout.cli.filesystem_task import prepare_filesystem

        return await prepare_filesystem(runtime, workspace, message, owned, principal)
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
