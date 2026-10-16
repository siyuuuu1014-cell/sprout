from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.models import ProcessResult
from Sprout.orchestration.models import ExecutionNode
from Sprout.runtime.nodes import NodeExecutor
from Sprout.task.models import Task


async def test_required_backend_missing_never_uses_host_broker():
    executor = SimpleNamespace(_isolated_coding_required=True, _coding_dispatch=None,
                               _process_broker=SimpleNamespace(run=AsyncMock()))
    with pytest.raises(RuntimeError, match="no host fallback"):
        await NodeExecutor._run_verification_command(
            executor, None, ("python",), Path("."), Task(), 3,
            node=ExecutionNode(), command_slot="evaluation-0")
    executor._process_broker.run.assert_not_called()


async def test_dispatch_identity_once_no_transient_retry():
    dispatch = AsyncMock(return_value=ProcessResult(("docker",), exit_code=1))
    executor = SimpleNamespace(_isolated_coding_required=True, _coding_dispatch=dispatch,
                               _process_broker=SimpleNamespace(run=AsyncMock()))
    task, node = Task(), ExecutionNode()
    result = await NodeExecutor._run_verification_command(
        executor, None, ("python",), Path("."), task, 3,
        node=node, command_slot="evaluation-0")
    assert result.exit_code == 1
    dispatch.assert_awaited_once_with(task=task, node=node, command=("python",),
                                    cwd=Path("."), command_slot="evaluation-0")
    executor._process_broker.run.assert_not_called()
