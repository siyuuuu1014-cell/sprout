"""A node's owner-local clock excludes only explicit approval wait intervals."""
import asyncio
from dataclasses import replace

import pytest

from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeBudget, NodeType
from Sprout.orchestration.node_budget import active_seconds_remaining, current_node_budget
from Sprout.orchestration.service import SequentialOrchestrator
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.metadata import SqliteMetadataStore
from Sprout.task.models import Task


@pytest.mark.parametrize("mode", ["resume", "cancel", "timeout", "wrong_owner"])
async def test_active_clock_guard_and_no_replay(tmp_path, mode):
    store = SqliteMetadataStore(SqliteDatabase(tmp_path / "active.db"))
    node = ExecutionNode(task_id="active-clock", type=NodeType.EVALUATION,
                         budget=NodeBudget(timeout_seconds=.15))
    graph = ExecutionGraph(task_id=node.task_id, nodes=(node,), entry_node_ids=(node.id,))
    await store.save_task(Task(id=node.task_id, workspace_id="isolated"))
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(running):
        calls.append(running.id)
        clock = current_node_budget(running)
        assert clock is not None
        if mode == "wrong_owner":
            async def child():
                assert current_node_budget(running) is None
                with pytest.raises(PermissionError):
                    with clock.approval_wait():
                        pytest.fail("child acquired owner clock")
            await asyncio.create_task(child())
            assert active_seconds_remaining(replace(running, task_id="wrong")) is None
            return {"passed": True}
        with clock.approval_wait():
            entered.set()
            await release.wait()
        assert 0 < active_seconds_remaining(running) <= .15
        if mode == "timeout":
            await asyncio.sleep(1)
        return {"passed": True}

    runner = SequentialOrchestrator(store)
    running = asyncio.create_task(runner.execute(graph, handler=handler))
    try:
        if mode != "wrong_owner":
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.sleep(.25)
            assert not running.done()
            if mode == "cancel":
                running.cancel()
            else:
                release.set()
        if mode == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await running
        else:
            await asyncio.wait_for(running, 2)
        settled = await store.get_execution_node(node.id)
        expected = "uncertain" if mode in {"cancel", "timeout"} else "completed"
        assert settled.metadata["execution_guard"]["state"] == expected
        if expected == "completed":
            with pytest.raises(ValueError, match="completed -> running"):
                await runner.execute(graph, handler=handler)
        else:
            await runner.execute(graph, handler=handler)
        assert calls == [node.id]
        assert active_seconds_remaining(settled) is None
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        store.close()
