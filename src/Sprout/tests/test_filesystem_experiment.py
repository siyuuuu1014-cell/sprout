from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.coding_command_tool import IsolatedCodingCommandTool
from Sprout.execution.models import SandboxRef
from Sprout.sandbox.filesystem import FilesystemSandbox


@pytest.mark.asyncio
async def test_filesystem_coding_command_keeps_argv_and_confines_cwd(tmp_path):
    task = SimpleNamespace(id="task", source="cli", workspace_id="workspace",
                           actor=SimpleNamespace(user_id="user"))
    handler = SimpleNamespace(execute=AsyncMock())
    sandbox = SandboxRef(id="sandbox", kind="filesystem_experiment", root=tmp_path)
    tool = IsolatedCodingCommandTool(
        handler, task=task, node=object(), sandbox=sandbox, filesystem_root_only=True)
    result = await tool.invoke({"command": ["python", "--version"], "relative_cwd": "other"})
    assert not result.ok
    handler.execute.assert_not_awaited()


def test_git_command_requires_replay_plan_even_with_filesystem_option(tmp_path):
    task = SimpleNamespace(metadata={})
    sandbox = SandboxRef(id="sandbox", kind="git_worktree", root=tmp_path)
    with pytest.raises(ValueError, match="Filesystem experiment sandbox required"):
        IsolatedCodingCommandTool(
            object(), task=task, node=object(), sandbox=sandbox, filesystem_root_only=True)
    with pytest.raises(ValueError, match="Replay plan"):
        IsolatedCodingCommandTool(object(), task=task, node=object(), sandbox=sandbox)

def test_filesystem_sandbox_copies_exact_inputs_and_cleans_up(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    (original / "app.py").write_bytes(b"print(1)\r\n")
    (original / ".env").write_text("private")
    with FilesystemSandbox(original) as experiment:
        ref, receipts = experiment.create(("app.py",), lambda path: None)
        assert ref.kind == "filesystem_experiment"
        assert receipts["app.py"]["identity"]["size"] == 10
        assert (ref.root / "app.py").read_bytes() == b"print(1)\r\n"
        assert not (ref.root / ".env").exists()
        (ref.root / "app.py").write_text("experiment")
        assert (original / "app.py").read_bytes() == b"print(1)\r\n"
        with pytest.raises(PermissionError):
            experiment.create(("app.py",), lambda path: None)
    assert not ref.root.exists()


@pytest.mark.parametrize("path", ["../app.py", ".env", "secrets.db"])
def test_filesystem_sandbox_rejects_private_and_escaping_inputs(tmp_path, path):
    with FilesystemSandbox(tmp_path) as experiment:
        with pytest.raises((PermissionError, ValueError)):
            experiment.create((path,), lambda path: None)

def test_filesystem_sandbox_cleans_up_after_partial_capture(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    (original / "app.py").write_text("print(1)", encoding="utf-8")
    experiment = FilesystemSandbox(original)
    captured = []

    def authorize(path):
        captured.append(experiment.ref.root)
        if path.name == "blocked.py":
            raise PermissionError("blocked by fixture policy")

    with pytest.raises(PermissionError, match="blocked by fixture policy"):
        experiment.create(("app.py", "blocked.py"), authorize)
    assert captured
    assert not captured[0].exists()
    assert experiment.ref is None
    assert (original / "app.py").read_text(encoding="utf-8") == "print(1)"

async def test_filesystem_coding_confirms_pending_grant_in_owned_foreground():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.gateway.identity import Principal
    from Sprout.runtime.filesystem_experiment import FilesystemExperiment
    from Sprout.security.approval import ApprovalManager, ApprovalStatus
    from Sprout.storage.local.memory.operational import MemoryOperationalStore
    from Sprout.task.models import Task, TaskStatus

    manager = ApprovalManager(MemoryOperationalStore())
    actor = Principal("owner", authenticated=True, source="cli")
    task = Task(workspace_id="workspace", actor=actor, source="cli", status=TaskStatus.RUNNING)
    experiment = SimpleNamespace(
        _subject=AsyncMock(return_value=task), image="fixture",
        runtime=SimpleNamespace(approvals=manager), sandbox=SimpleNamespace(
            ref=SimpleNamespace(id="sandbox")),
        confirm_tool=AsyncMock(return_value=True),
    )
    grant = await FilesystemExperiment.approval(experiment, manager, task=task, node=object())
    assert grant.status is ApprovalStatus.APPROVED
    experiment.confirm_tool.assert_awaited_once()

async def test_completed_filesystem_task_survives_sandbox_cleanup_failure(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from Sprout.gateway.identity import Principal
    from Sprout.runtime.filesystem_experiment import FilesystemExperiment
    from Sprout.task.models import Task, TaskResult, TaskStatus
    from Sprout.workspace.models import Workspace

    workspace = Workspace("workspace", tmp_path)
    actor = Principal("owner", authenticated=True, source="cli")
    task = Task(workspace_id=workspace.id, actor=actor, metadata={"session_id": "session"})
    completed = TaskResult(task_id=task.id, status=TaskStatus.COMPLETED)
    store = SimpleNamespace(list_execution_nodes=AsyncMock(return_value=[]),
                            update_task_status=AsyncMock())
    lifecycle = SimpleNamespace(finalize=AsyncMock(side_effect=OSError("locked")),
                                close_filesystem=Mock())
    runtime = SimpleNamespace(
        get_workspace=AsyncMock(return_value=workspace),
        _sessions=SimpleNamespace(require_owner=AsyncMock(), frozen_principal=lambda _: actor),
        session_workspace_id=lambda _: workspace.id,
        _metadata_or_raise=lambda: store,
        _filesystem_trajectory=Mock(),
        _executor=lambda _: SimpleNamespace(handler=Mock()),
        _persist_node_products=AsyncMock(),
        _sandbox_lifecycle=lifecycle,
        _record_security=Mock(),
    )
    experiment = FilesystemExperiment(runtime, task, workspace, ("app.py",), None, tmp_path)
    assert experiment.owner is asyncio.current_task()
    monkeypatch.setattr("Sprout.runtime.filesystem_experiment.SequentialOrchestrator.execute",
                        AsyncMock(return_value=completed))
    result = await experiment.run(runtime, task)
    assert result.status is TaskStatus.COMPLETED
    assert result.metrics["sandbox_cleanup"] == "failed"
    store.update_task_status.assert_not_awaited()
    runtime._record_security.assert_called_once()
