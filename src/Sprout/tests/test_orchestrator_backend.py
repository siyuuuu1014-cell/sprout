"""Tests for the local orchestrator backend."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from Sprout.orchestration.models import (
    ExecutionGraph,
    ExecutionNode,
    NodeBudget,
    NodeStatus,
    NodeType,
)
from Sprout.orchestration.terminal.local import LocalOrchestratorBackend
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.metadata import SqliteMetadataStore
from Sprout.task.models import Task, TaskStatus


def test_local_orchestrator_backend_executes_graph(tmp_path: Path) -> None:
    store = SqliteMetadataStore(SqliteDatabase(tmp_path / "metadata.db"))
    node = ExecutionNode(task_id="task-1", type=NodeType.TOOL)
    graph = ExecutionGraph(task_id="task-1", nodes=(node,), entry_node_ids=(node.id,))

    async def run() -> None:
        await store.save_task(Task(id=graph.task_id, workspace_id="fixture"))
        result = await LocalOrchestratorBackend(store).execute(graph)
        assert result.status is TaskStatus.COMPLETED
        persisted = await store.get_execution_node(node.id)
        assert persisted.metadata["execution_guard"]["state"] == "completed"

    try:
        asyncio.run(run())
    finally:
        store.close()


def test_foreground_repair_exhaustion_stops_before_review(tmp_path: Path) -> None:
    store = SqliteMetadataStore(SqliteDatabase(tmp_path / "metadata.db"))
    node = ExecutionNode(task_id="task", type=NodeType.EVALUATION,
                         metadata={"foreground_repair_round": 1})
    review = ExecutionNode(task_id="task", type=NodeType.APPROVAL, dependencies=(node.id,))
    graph = ExecutionGraph(task_id="task", nodes=(node, review), entry_node_ids=(node.id,))
    called = []

    async def handler(step):
        called.append(step.id)
        return {"status": "failed", "verified": False}

    async def run():
        await store.save_task(Task(id="task", workspace_id="fixture"))
        result = await LocalOrchestratorBackend(store).execute(graph, handler=handler)
        assert result.status is TaskStatus.FAILED
        assert result.error == "foreground_repair_budget_exhausted"
        assert called == [node.id]
        persisted = await store.get_execution_node(node.id)
        assert persisted.metadata["output"]["status"] == "failed"

    try:
        asyncio.run(run())
    finally:
        store.close()


@pytest.mark.parametrize("previous_status", ["passed", "failed"])
async def test_foreground_repair_uses_settled_evaluation_after_reopen(
    tmp_path: Path, previous_status: str,
) -> None:
    database = tmp_path / "metadata.db"
    task = Task(id="task", workspace_id="fixture", status=TaskStatus.RUNNING)
    first = ExecutionNode(id="first", task_id=task.id, type=NodeType.EVALUATION,
                          metadata={"foreground_repair_round": 0})
    repair = ExecutionNode(id="repair", task_id=task.id, type=NodeType.AGENT,
                           dependencies=(first.id,))
    second = ExecutionNode(id="second", task_id=task.id, type=NodeType.EVALUATION,
                           dependencies=(repair.id,), metadata={"foreground_repair_round": 1})
    review = ExecutionNode(id="review", task_id=task.id, type=NodeType.APPROVAL,
                           dependencies=(second.id,))
    graph = ExecutionGraph(task_id=task.id, nodes=(first, repair, second, review),
                           entry_node_ids=(first.id,))
    original = SqliteMetadataStore(SqliteDatabase(database))
    try:
        await original.save_task(task)
        await original.save_execution_node(replace(first, status=NodeStatus.COMPLETED, metadata={
            **first.metadata, "output": {"status": previous_status},
        }))
    finally:
        original.close()

    called = []

    async def handler(node):
        called.append(node.id)
        return {"status": "passed"}

    reopened = SqliteMetadataStore(SqliteDatabase(database))
    try:
        result = await LocalOrchestratorBackend(reopened).execute(graph, handler=handler)
        assert result.status is TaskStatus.COMPLETED
        expected = ["review"] if previous_status == "passed" else ["repair", "second", "review"]
        assert called == expected
        stored = await reopened.get_execution_node(first.id)
        assert stored.metadata["output"] == {"status": previous_status}
    finally:
        reopened.close()


def test_local_orchestrator_enforces_node_timeout(tmp_path: Path) -> None:
    store = SqliteMetadataStore(SqliteDatabase(tmp_path / "metadata.db"))
    node = ExecutionNode(
        task_id="task-1",
        type=NodeType.TOOL,
        budget=NodeBudget(timeout_seconds=0.05),
    )
    graph = ExecutionGraph(task_id="task-1", nodes=(node,), entry_node_ids=(node.id,))

    called = []
    cancelled = []

    async def slow_handler(running: ExecutionNode) -> None:
        called.append(running.id)
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(running.id)

    async def run() -> None:
        await store.save_task(Task(id=graph.task_id, workspace_id="fixture"))
        backend = LocalOrchestratorBackend(store)
        result = await backend.execute(graph, handler=slow_handler)
        # A real owned execution timeout is uncertain, not proof of no effects.
        assert result.status is TaskStatus.WAITING_RESOURCE
        assert result.error is not None
        assert "TimeoutError" in result.error
        assert called == cancelled == [node.id]
        persisted = await store.get_execution_node(node.id)
        assert persisted.metadata["execution_guard"]["state"] == "uncertain"
        assert "TimeoutError" in persisted.metadata["error"]
        repeated = await backend.execute(graph, handler=slow_handler)
        assert repeated.status is TaskStatus.WAITING_RESOURCE
        assert called == [node.id]

    try:
        asyncio.run(run())
    finally:
        store.close()


def test_orchestrator_skips_verify_loops_after_a_green_evaluation(
    tmp_path: Path,
) -> None:
    store = SqliteMetadataStore(SqliteDatabase(tmp_path / "metadata.db"))
    eval_one = ExecutionNode(
        id="task-1:evaluation",
        task_id="task-1",
        type=NodeType.EVALUATION,
    )
    agent_two = ExecutionNode(
        id="task-1:agent:1",
        task_id="task-1",
        type=NodeType.AGENT,
        dependencies=(eval_one.id,),
    )
    eval_two = ExecutionNode(
        id="task-1:evaluation:1",
        task_id="task-1",
        type=NodeType.EVALUATION,
        dependencies=(agent_two.id,),
    )
    approval = ExecutionNode(
        id="task-1:approval",
        task_id="task-1",
        type=NodeType.APPROVAL,
        dependencies=(eval_two.id,),
    )
    apply = ExecutionNode(
        id="task-1:apply",
        task_id="task-1",
        type=NodeType.APPLY,
        dependencies=(approval.id,),
    )
    graph = ExecutionGraph(
        task_id="task-1",
        nodes=(eval_one, agent_two, eval_two, approval, apply),
        entry_node_ids=(eval_one.id,),
    )
    called: list[str] = []

    async def handler(node: ExecutionNode) -> dict:
        called.append(node.id)
        if node.type is NodeType.EVALUATION:
            return {"status": "passed"}
        return {}

    async def run() -> None:
        await store.save_task(Task(id=graph.task_id, workspace_id="fixture"))
        result = await LocalOrchestratorBackend(store).execute(
            graph, handler=handler
        )
        assert result.status is TaskStatus.COMPLETED
        assert called == [
            "task-1:evaluation",
            "task-1:approval",
            "task-1:apply",
        ]
        for skipped in (agent_two, eval_two):
            assert (await store.get_execution_node(skipped.id)).status.value == "skipped"

    try:
        asyncio.run(run())
    finally:
        store.close()
