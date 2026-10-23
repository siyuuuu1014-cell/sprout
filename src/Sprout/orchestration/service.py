"""Sequential execution graph runner with resume, retry, and budgets.

The runner owns node scheduling. It restores node status from the metadata
store first, so re-running a task resumes it instead of repeating finished
work (SEMA spec 19.2). Approval nodes park the graph in WAITING_APPROVAL
until a human decides (spec 8.3, 11.5).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from Sprout.orchestration.execution_guard import NodeExecutionGuard
from Sprout.orchestration.models import (
    ExecutionGraph,
    ExecutionNode,
    NodeStatus,
    NodeType,
)
from Sprout.runtime.state import NodeStateMachine, TaskStateMachine
from Sprout.storage.contracts.metadata import MetadataStore
from Sprout.task.models import TaskBudget, TaskResult, TaskStatus
from Sprout.trajectory.models import TrajectoryEvent

NodeHandler = Callable[[ExecutionNode], Awaitable[Any] | Any] | None


@dataclass(frozen=True, slots=True)
class NodeResult:
    node_id: str
    status: NodeStatus
    output: Any = None
    error: str | None = None


class SequentialOrchestrator:
    """Runs graph nodes in declaration order.

    Sequential execution keeps the first implementation deterministic and
    auditable; the graph model already carries dependencies for a future
    parallel scheduler.
    """

    def __init__(self, metadata: MetadataStore) -> None:
        self._metadata = metadata

    async def execute(
        self,
        graph: ExecutionGraph,
        *,
        handler: NodeHandler = None,
        recorder: Any | None = None,
        budget: TaskBudget | None = None,
    ) -> TaskResult:
        # A local scheduler must not bypass external activity ownership after
        # a configuration change, even when the caller omits that node.
        stored = await self._metadata.list_execution_nodes(graph.task_id)
        if any(self._blocks_local_replay(node) for node in stored):
            current = await self._metadata.get_task(graph.task_id)
            if current is not None and TaskStateMachine.can_transition(
                current.status,
                TaskStatus.WAITING_RESOURCE,
            ):
                await self._metadata.update_task_status(graph.task_id, TaskStatus.WAITING_RESOURCE)
            return TaskResult(
                task_id=graph.task_id,
                status=TaskStatus.WAITING_RESOURCE,
                error="Unconfirmed node ownership requires reconciliation; no local replay",
            )
        nodes = await self._restore(graph)
        deadline = None
        if budget is not None and budget.max_duration_seconds > 0:
            deadline = time.monotonic() + budget.max_duration_seconds

        await self._update_task_status(graph.task_id, TaskStatus.RUNNING, recorder)
        await self._record(recorder, "task.started", graph.task_id)

        statuses = {node.id: node.status for node in nodes}
        nodes = list(nodes)
        index = 0
        while index < len(nodes):
            node = nodes[index]
            if node.status in {NodeStatus.COMPLETED, NodeStatus.SKIPPED}:
                index += 1
                continue
            if not self._dependencies_completed(node, statuses):
                return await self._abort(
                    recorder, node, graph.task_id, "Dependencies are not completed"
                )

            if deadline is not None and time.monotonic() > deadline:
                await self._record(recorder, "task.budget_exceeded", graph.task_id)
                return await self._abort(
                    recorder, node, graph.task_id, "Task budget exceeded (duration)"
                )

            outcome = await self._run_node(
                node, graph.task_id, handler, recorder, resume_key=graph.dispatch_key,
            )
            statuses[node.id] = outcome.status

            if outcome.status is NodeStatus.WAITING:
                resource_wait = (isinstance(outcome.output, dict)
                                 and outcome.output.get("waiting_kind") == "resource")
                target = (TaskStatus.WAITING_RESOURCE if resource_wait
                          else TaskStatus.WAITING_APPROVAL)
                await self._update_task_status(
                    graph.task_id,
                    target,
                    recorder,
                )
                await self._record(
                    recorder,
                    "task.waiting_resource" if resource_wait else "task.waiting_approval",
                    graph.task_id,
                    {"node_id": node.id, "type": node.type.value},
                )
                return TaskResult(task_id=graph.task_id, status=target, error=outcome.error)

            if outcome.status is NodeStatus.CANCELLED:
                await self._update_task_status(graph.task_id, TaskStatus.CANCELLED, recorder)
                return TaskResult(task_id=graph.task_id, status=TaskStatus.CANCELLED)

            if outcome.status is NodeStatus.FAILED:
                await self._update_task_status(graph.task_id, TaskStatus.FAILED, recorder)
                await self._record(recorder, "task.failed", graph.task_id)
                return TaskResult(
                    task_id=graph.task_id,
                    status=TaskStatus.FAILED,
                    error=outcome.error,
                )

            evaluation_status = (
                outcome.output.get("status")
                if outcome.status is NodeStatus.COMPLETED
                and node.type is NodeType.EVALUATION
                and isinstance(outcome.output, dict)
                else None
            )
            # Withheld checks cannot be repaired by rewriting code. Preserve
            # their incomplete status for the shared apply gate, never green it.
            if evaluation_status in {"passed", "not_verified", "partially_verified"}:
                index += 1
                while index < len(nodes) and nodes[index].type in {
                    NodeType.AGENT,
                    NodeType.EVALUATION,
                }:
                    skipped = nodes[index]
                    statuses[skipped.id] = NodeStatus.SKIPPED
                    await self._skip_node(skipped, graph.task_id, recorder)
                    index += 1
                continue
            index += 1

        await self._update_task_status(graph.task_id, TaskStatus.COMPLETED, recorder)
        await self._record(recorder, "task.completed", graph.task_id)
        return TaskResult(task_id=graph.task_id, status=TaskStatus.COMPLETED)

    async def resume(
        self,
        graph: ExecutionGraph,
        *,
        handler: NodeHandler = None,
        recorder: Any | None = None,
        budget: TaskBudget | None = None,
    ) -> TaskResult:
        """Continue a graph parked in WAITING_APPROVAL after a human decision."""
        return await self.execute(graph, handler=handler, recorder=recorder, budget=budget)

    # -- node execution ------------------------------------------------------
    @staticmethod
    def _blocks_local_replay(node: ExecutionNode) -> bool:
        guard = node.metadata.get("execution_guard", {})
        if not isinstance(guard, dict):
            return True
        if (node.status in {NodeStatus.RUNNING, NodeStatus.FAILED}
                or guard.get("state") in {"active", "uncertain"}):
            return True
        if node.status is NodeStatus.WAITING:
            output = node.metadata.get("output")
            return not (guard.get("owner") and guard.get("state") == "waiting"
                        and isinstance(output, dict) and output.get("waiting") is True)
        return False

    async def _run_node(
        self,
        node: ExecutionNode,
        task_id: str,
        handler: NodeHandler,
        recorder: Any | None,
        *,
        resume_key: str = "",
    ) -> NodeResult:
        async def invoke(running: ExecutionNode):
            await self._record(
                recorder,
                "node.started",
                task_id,
                {
                    "node_id": running.id,
                    "type": running.type.value,
                    "attempt": running.attempts,
                },
            )
            if handler is None:
                return None

            async def call_handler():
                result = handler(running)
                return await result if hasattr(result, "__await__") else result

            if running.budget.timeout_seconds > 0:
                return await asyncio.wait_for(
                    call_handler(), timeout=running.budget.timeout_seconds,
                )
            return await call_handler()

        # Claim, cached results, cancellation and uncertain effects all retain
        # the existing guard's semantics. Never rewrite an owned row or turn
        # an exception back into PENDING for an automatic effect retry.
        result = await NodeExecutionGuard(self._metadata).execute(
            node, invoke, resume_key=resume_key,
        )
        status = NodeStatus(result["status"])
        output = result.get("output")
        event_name = ("node.completed" if status is NodeStatus.COMPLETED else
                      "node.cancelled" if status is NodeStatus.CANCELLED else "node.waiting")
        await self._record(
            recorder, event_name,
            task_id, {"node_id": node.id, "type": node.type.value},
        )
        return NodeResult(node.id, status, output=output,
                          error=output.get("error") if isinstance(output, dict) else None)

    # -- state helpers -------------------------------------------------------
    async def _restore(self, graph: ExecutionGraph) -> tuple[ExecutionNode, ...]:
        """Overlay persisted node state onto a freshly compiled graph."""
        stored = {
            node.id: node for node in await self._metadata.list_execution_nodes(graph.task_id)
        }
        if not stored:
            return tuple(graph.nodes)
        restored: list[ExecutionNode] = []
        for node in graph.nodes:
            previous = stored.get(node.id)
            if previous is None:
                restored.append(node)
                continue
            restored.append(
                replace(
                    node,
                    status=previous.status,
                    attempts=previous.attempts,
                    metadata=dict(previous.metadata),
                )
            )
        return tuple(restored)

    async def _abort(
        self,
        recorder: Any | None,
        node: ExecutionNode,
        task_id: str,
        reason: str,
    ) -> TaskResult:
        current = await self._metadata.get_execution_node(node.id)
        if current is not None and current.metadata.get("execution_guard"):
            await self._update_task_status(task_id, TaskStatus.WAITING_RESOURCE, recorder)
            return TaskResult(task_id=task_id, status=TaskStatus.WAITING_RESOURCE, error=reason)
        await self._fail(node, reason)
        await self._record(
            recorder,
            "node.failed",
            task_id,
            {"node_id": node.id, "error": reason},
        )
        await self._update_task_status(task_id, TaskStatus.FAILED, recorder)
        await self._record(recorder, "task.failed", task_id)
        return TaskResult(task_id=task_id, status=TaskStatus.FAILED, error=reason)

    async def _fail(self, node: ExecutionNode, reason: str) -> None:
        NodeStateMachine.validate(node.status, NodeStatus.FAILED)
        failed = replace(
            node,
            status=NodeStatus.FAILED,
            metadata={**node.metadata, "error": reason},
        )
        await self._metadata.save_execution_node(failed)

    async def _skip_node(
        self,
        node: ExecutionNode,
        task_id: str,
        recorder: Any | None,
    ) -> None:
        current = await self._metadata.get_execution_node(node.id)
        if current is not None and current.metadata.get("execution_guard"):
            if current.status in {NodeStatus.COMPLETED, NodeStatus.SKIPPED}:
                return
            raise PermissionError("Owned node cannot be skipped by the local scheduler")
        skipped = replace(
            node,
            status=NodeStatus.SKIPPED,
            metadata={**node.metadata, "output": {"skipped": True}},
        )
        await self._metadata.save_execution_node(skipped)
        await self._record(
            recorder,
            "node.skipped",
            task_id,
            {"node_id": node.id, "type": node.type.value},
        )

    async def _update_task_status(
        self,
        task_id: str,
        target: TaskStatus,
        recorder: Any | None,
    ) -> None:
        current_task = await self._metadata.get_task(task_id)
        if current_task is not None:
            TaskStateMachine.validate(current_task.status, target)
        await self._metadata.update_task_status(task_id, target)
        await self._record(
            recorder,
            "task.status_changed",
            task_id,
            {
                "previous": (current_task.status.value if current_task is not None else None),
                "target": target.value,
            },
        )

    @staticmethod
    def _dependencies_completed(node: ExecutionNode, statuses: dict[str, NodeStatus]) -> bool:
        return all(
            statuses.get(dep) in {NodeStatus.COMPLETED, NodeStatus.SKIPPED}
            for dep in node.dependencies
        )

    @staticmethod
    async def _record(
        recorder: Any | None,
        name: str,
        task_id: str,
        payload: dict | None = None,
    ) -> None:
        if recorder is None:
            return
        await recorder.record(TrajectoryEvent(name=name, task_id=task_id, payload=payload or {}))
