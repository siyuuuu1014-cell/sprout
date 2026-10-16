from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.execution.coding_dispatch import IsolatedNodeDispatcher
from Sprout.execution.models import ProcessResult
from Sprout.orchestration.models import ExecutionNode
from Sprout.task.models import Task
from Sprout.tests.test_coding_container import fixture


async def test_dispatch_binds_node_owner_and_stable_slot(tmp_path, monkeypatch):
    task = Task()
    node = ExecutionNode(task_id=task.id, metadata={"execution_guard": {
        "owner": "owner", "state": "active"}})
    request = replace(fixture(tmp_path), task_id=task.id)
    factory = Mock(return_value=request)
    execute = AsyncMock(return_value=ProcessResult(request.command, exit_code=0))
    monkeypatch.setattr("Sprout.execution.coding_dispatch.DockerCodingBackend.execute", execute)
    result = await IsolatedNodeDispatcher(AsyncMock(), AsyncMock(), factory)(
        task=task, node=node, command=request.command, cwd=Path("."), command_slot="evaluation-0")
    assert result.exit_code == 0
    assert execute.await_count == 1 and execute.call_args.kwargs["node_id"] == node.id
    assert factory.call_args.kwargs["command_slot"] == "evaluation-0"


@pytest.mark.parametrize("slot", ["arbitrary", "../0", "evaluation--1", "evaluation-0/new"])
async def test_non_host_slot_cannot_reach_request_factory(slot):
    task = Task()
    node = ExecutionNode(task_id=task.id, metadata={"execution_guard": {
        "owner": "owner", "state": "active"}})
    factory = Mock()
    with pytest.raises(ValueError, match="Host-generated"):
        await IsolatedNodeDispatcher(AsyncMock(), AsyncMock(), factory)(
            task=task, node=node, command=("python",), cwd=Path("."), command_slot=slot)
    factory.assert_not_called()


async def test_factory_cannot_silently_change_command(tmp_path):
    task = Task()
    node = ExecutionNode(task_id=task.id, metadata={"execution_guard": {
        "owner": "owner", "state": "active"}})
    request = replace(fixture(tmp_path), task_id=task.id)
    with pytest.raises(ValueError, match="command changed"):
        await IsolatedNodeDispatcher(AsyncMock(), AsyncMock(), Mock(return_value=request))(
            task=task, node=node, command=("different",), cwd=Path("."), command_slot="baseline-0")
