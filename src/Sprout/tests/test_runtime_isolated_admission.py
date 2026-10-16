"""Initial and approval-resume admission must not bypass isolation."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.runtime.runtime import Runtime
from Sprout.task.models import Task, TaskStatus


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["initial", "resume"])
@pytest.mark.parametrize("dispatcher", [None, False, "not-a-dispatcher"])
@pytest.mark.parametrize("status", [TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL,
                                  TaskStatus.COMPLETED, TaskStatus.CANCELLED,
                                  TaskStatus.ROLLED_BACK])
async def test_missing_dispatcher_blocks_both_entries(entry, dispatcher, status):
    task = Task(workspace_id="fixture", instruction="fixture", status=status)
    metadata = SimpleNamespace(get_task=AsyncMock(return_value=task),
                               update_task_status=AsyncMock(),
                               get_workspace=AsyncMock())
    runtime = Runtime.__new__(Runtime)
    runtime.storage = SimpleNamespace(metadata=metadata)
    runtime._isolated_coding_required = True
    runtime._isolated_coding_dispatch = dispatcher
    runtime._scanner = Mock()
    if entry == "initial":
        result = await runtime._execute_task(task, artifact_snapshot=None)
    else:
        result = await runtime.resume_task(task)
    terminal = status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ROLLED_BACK}
    assert result.status == (status if terminal else TaskStatus.WAITING_RESOURCE)
    if terminal:
        metadata.update_task_status.assert_not_awaited()
    else:
        metadata.update_task_status.assert_awaited_once_with(task.id, TaskStatus.WAITING_RESOURCE)
        assert "no host fallback" in result.error
    metadata.get_workspace.assert_not_awaited()
    runtime._scanner.scan.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", [{"replay_plan": None}, {"replay_source_task_id": "source"}])
@pytest.mark.parametrize("missing", ["mode", "dispatcher", "session"])
async def test_worker_blocks_experiment_before_claim_or_executor(marker, missing):
    task = Task(workspace_id="fixture", instruction="fixture", status=TaskStatus.RUNNING,
                metadata={"execution_mode": "code", **marker})
    node = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
    metadata = SimpleNamespace(get_task=AsyncMock(return_value=task),
        get_workspace=AsyncMock(return_value=object()),
        get_execution_node=AsyncMock(return_value=None), claim_execution_node=AsyncMock())
    runtime = Runtime.__new__(Runtime)
    runtime.storage = SimpleNamespace(metadata=metadata)
    runtime._task_for_execution = Mock(return_value=task)
    runtime._isolated_coding_required = missing != "mode"
    runtime._isolated_coding_dispatch = None if missing == "dispatcher" else Mock()
    runtime._isolated_coding_session_factory = None if missing == "session" else Mock()
    runtime._executor = Mock()
    result = await runtime.execute_node(node)
    assert result["status"] == "waiting_resource"
    assert result["output"]["waiting"] is True
    assert "no host fallback" in result["output"]["error"]
    metadata.claim_execution_node.assert_not_awaited()
    runtime._executor.assert_not_called()


@pytest.mark.asyncio
async def test_unassembled_worker_can_return_completed_experiment_evidence():
    task = Task(workspace_id="fixture", instruction="fixture", status=TaskStatus.RUNNING,
                metadata={"execution_mode": "code", "replay_plan": None})
    node = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
    completed = replace(node, status=NodeStatus.COMPLETED, metadata={"output": {"value": "cached"}})
    metadata = SimpleNamespace(get_task=AsyncMock(return_value=task),
        get_workspace=AsyncMock(return_value=object()),
        get_execution_node=AsyncMock(return_value=completed))
    runtime = Runtime.__new__(Runtime)
    runtime.storage = SimpleNamespace(metadata=metadata)
    runtime._task_for_execution = Mock(return_value=task)
    runtime._executor = Mock()
    result = await runtime.execute_node(node)
    assert result["status"] == "completed"
    assert result["output"] == {"value": "cached"}
    runtime._executor.assert_not_called()


@pytest.mark.asyncio
async def test_admission_never_invokes_dispatcher_or_enables_normal_tasks():
    runtime = Runtime.__new__(Runtime)
    runtime._isolated_coding_required = True
    runtime._isolated_coding_dispatch = Mock()
    task = Task(workspace_id="fixture", instruction="fixture")
    assert await runtime._isolated_execution_wait(task) is None
    runtime._isolated_coding_dispatch.assert_not_called()
    runtime._isolated_coding_required = False
    runtime._isolated_coding_dispatch = None
    assert await runtime._isolated_execution_wait(task) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("status", [TaskStatus.COMPLETED, TaskStatus.CANCELLED,
                                  TaskStatus.ROLLED_BACK])
async def test_resume_stale_snapshot_cannot_reopen_terminal_task(isolated, status):
    stale = Task(workspace_id="stale", instruction="old", status=TaskStatus.WAITING_APPROVAL)
    current = replace(stale, workspace_id="current", status=status)
    metadata = SimpleNamespace(get_task=AsyncMock(return_value=current),
                               get_workspace=AsyncMock(), update_task_status=AsyncMock())
    runtime = Runtime.__new__(Runtime)
    runtime.storage = SimpleNamespace(metadata=metadata)
    runtime._isolated_coding_required = isolated
    runtime._isolated_coding_dispatch = Mock()
    assert (await runtime.resume_task(stale)).status == status
    metadata.get_workspace.assert_not_awaited()
    metadata.update_task_status.assert_not_awaited()
    runtime._isolated_coding_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_resume_uses_persisted_workspace_and_task_binding():
    stale = Task(workspace_id="stale", instruction="old", status=TaskStatus.WAITING_APPROVAL)
    current = replace(stale, workspace_id="current", instruction="updated")
    workspace = object()
    metadata = SimpleNamespace(get_task=AsyncMock(return_value=current),
                               get_workspace=AsyncMock(return_value=workspace))
    runtime = Runtime.__new__(Runtime)
    runtime.storage = SimpleNamespace(metadata=metadata)
    # Stop before graph assembly; assert the actual resume entry's admission
    # and task normalization inputs, not a helper called directly by the test.
    runtime._task_for_execution = Mock(side_effect=ValueError("binding probe"))
    with pytest.raises(ValueError, match="binding probe"):
        await runtime.resume_task(stale)
    metadata.get_workspace.assert_awaited_once_with("current")
    runtime._task_for_execution.assert_called_once_with(current, workspace)


@pytest.mark.asyncio
async def test_resume_missing_record_fails_before_workspace_access():
    task = Task(workspace_id="fixture", instruction="fixture")
    metadata = SimpleNamespace(get_task=AsyncMock(return_value=None), get_workspace=AsyncMock())
    runtime = Runtime.__new__(Runtime)
    runtime.storage = SimpleNamespace(metadata=metadata)
    with pytest.raises(LookupError, match="Task not found"):
        await runtime.resume_task(task)
    metadata.get_workspace.assert_not_awaited()
