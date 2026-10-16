from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.coding_commands import PlanBoundCodingCommands
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.tests.test_coding_assembly import fixture


async def prepared(tmp_path, monkeypatch):
    store, runtime, task, evaluation, sandbox = await fixture(tmp_path, monkeypatch)
    await store.finish_execution_node(replace(evaluation, status=NodeStatus.COMPLETED),
                                      "new-owner", "completed")
    record = ExecutionNode(task_id=task.id, type=NodeType.SANDBOX)
    await store.save_execution_node(record)
    assert await store.claim_execution_node(record, "sandbox-owner", "")
    record = await store.get_execution_node(record.id)
    output = {"id": sandbox.id, "kind": sandbox.kind, "root": str(sandbox.root),
              "worktree_ref": sandbox.worktree_ref}
    assert await store.finish_execution_node(replace(record, status=NodeStatus.COMPLETED,
        metadata={**record.metadata, "output": output}), "sandbox-owner", "completed")
    agent = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
    await store.save_execution_node(agent)
    assert await store.claim_execution_node(agent, "agent-owner", "")
    agent = await store.get_execution_node(agent.id)
    execute = AsyncMock(return_value={"process": "fixture", "imported": False,
                                      "import": {"waiting": True}})
    monkeypatch.setattr(
        "Sprout.execution.coding_session.CodingNodeSession.execute_command", execute)
    return store, task, agent, sandbox, PlanBoundCodingCommands(runtime, task), execute


async def test_mapping_preserves_process_and_incomplete_import(tmp_path, monkeypatch):
    store, task, node, sandbox, commands, execute = await prepared(tmp_path, monkeypatch)
    try:
        result = await commands.execute(task=task, node=node, command=("python", "script.py"),
                                        cwd=sandbox.root, command_slot="evaluation-0")
        assert result["imported"] is False and result["import"]["waiting"] is True
        execute.assert_awaited_once_with(("python", "script.py"), cwd=sandbox.root.resolve(),
                                        command_slot="evaluation-0")
    finally:
        store.close()


@pytest.mark.parametrize("change", ["role", "cwd", "task", "slot", "owner", "command"])
async def test_invalid_mapping_cannot_reach_session(tmp_path, monkeypatch, change):
    store, task, node, sandbox, commands, execute = await prepared(tmp_path, monkeypatch)
    try:
        cwd, slot, command = sandbox.root, "evaluation-0", ("python", "script.py")
        if change == "role":
            node = replace(node, type=NodeType.EVALUATION)
        elif change == "cwd":
            cwd = tmp_path
        elif change == "task":
            task = replace(task, instruction="changed")
        elif change == "slot":
            slot = "baseline-0"
        elif change == "owner":
            node = replace(node, metadata={"execution_guard": {
                "owner": "forged", "state": "active"}})
        else:
            command = "python script.py"
        with pytest.raises(ValueError):
            await commands.execute(task=task, node=node, command=command,
                                   cwd=cwd, command_slot=slot)
        execute.assert_not_called()
    finally:
        store.close()
