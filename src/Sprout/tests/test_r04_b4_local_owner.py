"""Local scheduling must delegate ownership and settlement to the existing guard."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from Sprout.orchestration.execution_guard import NodeExecutionGuard
from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeStatus, NodeType
from Sprout.orchestration.service import SequentialOrchestrator
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b3_node_publication import publication as publication
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.fixture
async def local():
    store = open_metadata_store(":memory:")
    task = Task(id="local-owner-task", workspace_id="controlled", status=TaskStatus.READY)
    await store.save_task(task)
    node = ExecutionNode(id="local-owner-node", task_id=task.id, type=NodeType.AGENT)
    graph = ExecutionGraph(task_id=task.id, nodes=(node,))
    yield store, node, graph, SequentialOrchestrator(store)
    store.close()


async def test_local_handler_receives_persisted_owner_and_guard_settles(local, monkeypatch):
    store, node, graph, runner = local
    calls = []
    save = store.save_execution_node

    async def no_generic_owned_write(value):
        assert not value.metadata.get("execution_guard"), "generic save overwrites an owner"
        return await save(value)

    monkeypatch.setattr(store, "save_execution_node", no_generic_owned_write)

    async def handler(running):
        saved = await store.get_execution_node(node.id)
        assert running == saved
        guard = saved.metadata["execution_guard"]
        assert guard["owner"] and guard["state"] == "active"
        calls.append(guard["owner"])
        return {"answer": "controlled"}

    result = await runner.execute(graph, handler=handler)
    assert result.status is TaskStatus.COMPLETED
    saved = await store.get_execution_node(node.id)
    assert saved.metadata["execution_guard"]["owner"] == calls[0]
    assert saved.metadata["execution_guard"]["state"] == "completed"
    assert saved.metadata["output"] == {"answer": "controlled"}
    assert saved.attempts == 1


async def test_guard_completed_node_is_not_executed_again(local):
    store, node, graph, runner = local
    await NodeExecutionGuard(store).execute(node, AsyncMock(return_value={"saved": True}))
    before = await store.get_execution_node(node.id)
    handler = AsyncMock(side_effect=AssertionError("completed effect replayed"))
    assert (await runner.execute(graph, handler=handler)).status is TaskStatus.COMPLETED
    assert await store.get_execution_node(node.id) == before
    handler.assert_not_called()


async def test_known_wait_is_cached_until_new_server_dispatch_key(local):
    store, node, graph, runner = local
    calls = []

    async def handler(running):
        calls.append(running.metadata["execution_guard"]["owner"])
        return {"waiting": True, "approval_id": "controlled"} if len(calls) == 1 else {"ok": True}

    assert (await runner.execute(graph, handler=handler)).status is TaskStatus.WAITING_APPROVAL
    first = await store.get_execution_node(node.id)
    assert first.metadata["execution_guard"]["state"] == "waiting"
    assert (await runner.resume(graph, handler=handler)).status is TaskStatus.WAITING_APPROVAL
    assert await store.get_execution_node(node.id) == first
    # This is host control-plane input, not a model-supplied approval token.
    decided = replace(graph, dispatch_key="server-confirmed-new-decision")
    assert (await runner.resume(decided, handler=handler)).status is TaskStatus.COMPLETED
    assert len(calls) == 2 and calls[0] != calls[1]


async def test_exception_is_resource_unknown_and_never_retried(local):
    store, node, graph, runner = local
    handler = AsyncMock(side_effect=TimeoutError("acknowledgement lost"))
    result = await runner.execute(graph, handler=handler)
    assert result.status is TaskStatus.WAITING_RESOURCE
    saved = await store.get_execution_node(node.id)
    assert saved.metadata["execution_guard"]["state"] == "uncertain"
    result = await runner.resume(replace(graph, dispatch_key="different"), handler=handler)
    assert result.status is TaskStatus.WAITING_RESOURCE
    assert await store.get_execution_node(node.id) == saved
    assert handler.await_count == 1


@pytest.mark.parametrize("guarded", [False, True])
async def test_old_running_node_is_not_reset_or_claimed(local, guarded):
    store, node, graph, runner = local
    if guarded:
        assert await store.claim_execution_node(node, "existing-owner", "original")
    else:
        await store.save_execution_node(replace(node, status=NodeStatus.RUNNING))
    before = await store.get_execution_node(node.id)
    handler = AsyncMock(side_effect=AssertionError("old running effect replayed"))
    result = await runner.execute(graph, handler=handler)
    assert result.status is TaskStatus.WAITING_RESOURCE
    assert await store.get_execution_node(node.id) == before
    handler.assert_not_called()


async def test_cancelled_handler_leaves_guard_uncertain(local):
    store, node, graph, runner = local
    entered = asyncio.Event()

    async def handler(running):
        entered.set()
        await asyncio.Event().wait()

    executing = asyncio.create_task(runner.execute(graph, handler=handler))
    await entered.wait()
    executing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await executing
    saved = await store.get_execution_node(node.id)
    assert saved.metadata["execution_guard"]["state"] == "uncertain"
    assert saved.attempts == 1


async def test_cached_cancelled_node_does_not_complete_task(local):
    store, node, graph, runner = local
    await store.save_execution_node(replace(node, status=NodeStatus.CANCELLED))
    handler = AsyncMock(side_effect=AssertionError("cancelled node replayed"))
    assert (await runner.execute(graph, handler=handler)).status is TaskStatus.CANCELLED
    handler.assert_not_called()


@pytest.mark.parametrize("initial_status", [TaskStatus.READY, TaskStatus.CREATED])
async def test_code_graph_uses_current_task_not_submission_snapshot(
    rig, captured, publication, monkeypatch, initial_status,
):
    from Sprout.orchestration.compiler import ExecutionGraphBuilder
    from Sprout.workspace.models import ReadPlan, ResourceRef, WorkspaceManifest

    assert await rig.storage.metadata.finish_execution_node(
        replace(rig.node, status=NodeStatus.COMPLETED), "fixture-owner", "completed")
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    task = replace(rig.task, status=initial_status)
    await rig.storage.metadata.save_task(task)
    executor, messages = publication
    executor._sandbox = AsyncMock(return_value={"status": "not_applicable"})
    executor._evaluation = AsyncMock(return_value={"status": "passed"})
    executor._approval = AsyncMock(return_value={"approved": True})
    executor._apply = AsyncMock(return_value={"status": "controlled-no-effect"})
    plan = ReadPlan(task_id=task.id, resources=(ResourceRef(rig.workspace.id, "src/main.py"),))
    graph = ExecutionGraphBuilder().build(task, plan, WorkspaceManifest(rig.workspace.id),
        steps=({"description": "inspect main"},))
    result = await SequentialOrchestrator(rig.storage.metadata).execute(
        graph, handler=executor.handler(task, rig.workspace, plan))
    assert result.status is TaskStatus.COMPLETED, result
    assert "def main" in repr(messages)
    assert "Findings from the plan" in repr(messages)


async def test_timeout_does_not_use_legacy_retry_budget(local):
    from Sprout.orchestration.models import NodeBudget

    store, node, graph, runner = local
    node = replace(node, budget=NodeBudget(max_attempts=3, timeout_seconds=0.01))
    graph = replace(graph, nodes=(node,))
    calls = []

    async def handler(running):
        calls.append(running.id)
        await asyncio.Event().wait()

    assert (await runner.execute(graph, handler=handler)).status is TaskStatus.WAITING_RESOURCE
    saved = await store.get_execution_node(node.id)
    assert saved.attempts == 1
    assert saved.metadata["execution_guard"]["state"] == "uncertain"
    assert calls == [node.id]


async def test_settlement_failure_leaves_owner_and_does_not_replay(local, monkeypatch):
    store, node, graph, runner = local
    handler = AsyncMock(return_value={"effect": "controlled"})
    monkeypatch.setattr(store, "finish_execution_node", AsyncMock(side_effect=OSError("lost ack")))
    with pytest.raises(OSError, match="lost ack"):
        await runner.execute(graph, handler=handler)
    saved = await store.get_execution_node(node.id)
    assert saved.metadata["execution_guard"]["state"] == "active"
    assert (await runner.resume(graph, handler=handler)).status is TaskStatus.WAITING_RESOURCE
    assert await store.get_execution_node(node.id) == saved
    assert handler.await_count == 1


async def test_runtime_decision_key_resumes_only_confirmed_approval(rig, monkeypatch):
    assert await rig.storage.metadata.finish_execution_node(
        replace(rig.node, status=NodeStatus.COMPLETED), "fixture-owner", "completed")
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    await rig.storage.metadata.save_task(replace(rig.task, status=TaskStatus.READY))
    manager = rig.runtime._approvals
    binding = {"task_id": rig.task.id, "requested_by": "owner", "source": "cli",
               "session_id": rig.session.id, "resource_scope": rig.workspace.id}
    pending = await manager.request("controlled", {}, **binding)
    node = ExecutionNode(id="approval-control", task_id=rig.task.id, type=NodeType.TOOL)
    graph = ExecutionGraph(task_id=rig.task.id, nodes=(node,))
    runner = SequentialOrchestrator(rig.storage.metadata)
    calls = []

    async def handler(running):
        calls.append(running.metadata["execution_guard"]["owner"])
        if not await manager.is_approved("controlled", {}, **binding):
            return {"waiting": True, "approval_id": pending.id}
        return {"approved": True}

    initial = await rig.runtime._restore_graph(graph)
    assert (await runner.execute(initial, handler=handler)).status is TaskStatus.WAITING_APPROVAL
    unchanged = await rig.runtime._restore_graph(graph)
    assert unchanged.dispatch_key == initial.dispatch_key
    assert (await runner.resume(unchanged, handler=handler)).status is TaskStatus.WAITING_APPROVAL
    assert len(calls) == 1
    await manager.decide(pending.id, True)
    approved = await rig.runtime._restore_graph(graph)
    assert approved.dispatch_key != initial.dispatch_key
    assert (await runner.resume(approved, handler=handler)).status is TaskStatus.COMPLETED
    assert len(calls) == 2 and calls[0] != calls[1]
