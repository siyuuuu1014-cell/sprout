from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.runtime.runtime import Runtime
from Sprout.task.models import Task, TaskStatus


def _runtime(metadata):
    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=metadata),
                              _isolated_coding_required=True, _isolated_coding_dispatch=None)
    # Exercise the real admission helper introduced by Runtime, not a fake result.
    runtime._isolated_execution_wait = MethodType(Runtime._isolated_execution_wait, runtime)
    return runtime


async def test_missing_backend_waits_before_workspace_or_nodes():
    task = Task()
    metadata = SimpleNamespace(get_task=AsyncMock(return_value=task),
                               update_task_status=AsyncMock(), get_workspace=AsyncMock())
    runtime = _runtime(metadata)
    result = await Runtime._execute_task(runtime, task, artifact_snapshot=None)
    assert result.status == TaskStatus.WAITING_RESOURCE
    metadata.update_task_status.assert_awaited_once_with(task.id, TaskStatus.WAITING_RESOURCE)
    metadata.get_workspace.assert_not_called()


@pytest.mark.parametrize("status", [TaskStatus.COMPLETED, TaskStatus.CANCELLED,
                                    TaskStatus.ROLLED_BACK])
async def test_preflight_does_not_reopen_terminal_task(status):
    task = Task(status=status)
    metadata = SimpleNamespace(get_task=AsyncMock(return_value=task),
                               update_task_status=AsyncMock())
    runtime = _runtime(metadata)
    result = await Runtime._execute_task(runtime, task, artifact_snapshot=None)
    assert result.status == status
    metadata.update_task_status.assert_not_called()
