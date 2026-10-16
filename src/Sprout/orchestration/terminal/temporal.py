"""Temporal-backed orchestrator backend.

This backend keeps the existing execution-graph model but delegates scheduling,
retries, approvals, and durability to Temporal workflows and activities.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
from dataclasses import dataclass
from typing import Any

from Sprout.orchestration.models import ExecutionGraph
from Sprout.orchestration.temporal_codec import graph_to_dict
from Sprout.runtime.state import TaskStateMachine
from Sprout.task.models import TaskResult, TaskStatus


@dataclass(slots=True)
class TemporalConfig:
    host: str = "127.0.0.1:7233"
    namespace: str = "default"
    task_queue: str = "sprout-tasks"

    @classmethod
    def from_env(cls) -> TemporalConfig:
        return cls(
            host=os.getenv("TEMPORAL_HOST", "127.0.0.1:7233"),
            namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
            task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "sprout-tasks"),
        )


class TemporalOrchestratorBackend:
    """Submit execution graphs to Temporal.

    The actual workflow/activity code lives in
    ``src/Sprout/orchestration/temporal_workflows.py`` and is executed by a
    Temporal worker process. This backend only performs client-side submission
    and result polling, so the CLI/runtime remain transport-agnostic.
    """

    def __init__(
        self,
        config: TemporalConfig | None = None,
        *,
        metadata: Any | None = None,
        result_wait_seconds: float = 30.0,
        wait_for_result: bool = True,
    ) -> None:
        if type(wait_for_result) is not bool:
            raise ValueError("Explicit boolean result-wait mode required")
        if (isinstance(result_wait_seconds, bool)
                or not isinstance(result_wait_seconds, (int, float))
                or not math.isfinite(result_wait_seconds) or result_wait_seconds <= 0):
            raise ValueError("Positive finite workflow result wait required")
        self._config = config or TemporalConfig.from_env()
        self._result_wait_seconds = float(result_wait_seconds)
        self._wait_for_result = wait_for_result
        self._metadata = metadata
        self._records: dict[str, dict[str, Any]] = {}

    def submission_only(self, *, metadata):
        """Independent client mode with the same configured route and store."""
        return TemporalOrchestratorBackend(
            self._config, metadata=metadata, wait_for_result=False)

    async def execute(
        self,
        graph: ExecutionGraph,
        *,
        handler=None,
        recorder=None,
        budget=None,
    ) -> TaskResult:
        from temporalio.client import Client, WorkflowFailureError
        from temporalio.common import WorkflowIDReusePolicy
        from temporalio.exceptions import WorkflowAlreadyStartedError

        if self._metadata is not None:
            task = await self._metadata.get_task(graph.task_id)
            if task is not None and task.status in {
                TaskStatus.COMPLETED,
                TaskStatus.CANCELLED,
                TaskStatus.ROLLED_BACK,
            }:
                return TaskResult(task_id=graph.task_id, status=task.status)

        records = (
            await self._metadata.list_task_submissions(graph.task_id)
            if self._metadata is not None
            else [r for r in self._records.values() if r["task_id"] == graph.task_id]
        )
        unresolved = next((r for r in records if r["state"] != "resolved"), None)
        terminal = next(
            (
                r
                for r in records
                if r.get("result", {}).get("status") in {"completed", "failed", "cancelled"}
            ),
            None,
        )
        record = unresolved or terminal
        if record is None:
            suffix = hashlib.sha256(graph.dispatch_key.encode()).hexdigest()[:24]
            record = {
                "id": f"sprout-{graph.task_id}-{suffix}",
                "task_id": graph.task_id,
                "state": "pending",
                "payload": graph_to_dict(graph),
                "host": self._config.host,
                "namespace": self._config.namespace,
                "task_queue": self._config.task_queue,
            }
            if self._metadata is not None:
                record = await self._metadata.ensure_task_submission(record)
            else:
                record = self._records.setdefault(record["id"], record)
        if record["state"] == "resolved":
            result = record["result"]
            if self._metadata is not None:
                await self._transition_task(graph.task_id, TaskStatus.RUNNING)
                await self._transition_task(graph.task_id, TaskStatus(result["status"]))
            return TaskResult(
                task_id=graph.task_id,
                status=TaskStatus(result["status"]),
                error=result.get("error"),
            )
        phase = "connect"
        try:
            client = await asyncio.wait_for(
                Client.connect(record["host"], namespace=record["namespace"]), timeout=10
            )
            phase = "start"
            if record.get("accepted"):
                handle = client.get_workflow_handle(record["id"], run_id=record.get("run_id"))
            else:
                try:
                    handle = await asyncio.wait_for(
                        client.start_workflow(
                            "SproutExecutionWorkflow",
                            record["payload"],
                            id=record["id"],
                            task_queue=record["task_queue"],
                            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                        ),
                        timeout=10,
                    )
                except WorkflowAlreadyStartedError as exc:
                    record = {**record, "run_id": exc.run_id}
                    handle = client.get_workflow_handle(record["id"], run_id=exc.run_id)
            record = {
                **record,
                "state": "accepted",
                "accepted": True,
                "run_id": record.get("run_id") or getattr(handle, "first_execution_run_id", None),
            }
            await self._save_record(record)
            if self._metadata is not None:
                await self._transition_task(graph.task_id, TaskStatus.RUNNING)
            if not self._wait_for_result:
                return TaskResult(task_id=graph.task_id, status=TaskStatus.RUNNING,
                    metrics={"submission_id": record["id"], "submission_state": "accepted",
                             "run_id": record["run_id"], "result_waited": False})
            phase = "result"
            # Client wait only: not an activity deadline or authority to rerun effects.
            result = await asyncio.wait_for(handle.result(), timeout=self._result_wait_seconds)
        except WorkflowFailureError as exc:
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:  # noqa: BLE001 - unify Temporal failures into TaskResult
            record = {
                **record,
                "state": "pending" if phase == "connect" else "uncertain",
                "phase": phase,
                "error": f"{type(exc).__name__}: {exc}",
            }
            await self._save_record(record)
            if self._metadata is not None:
                await self._transition_task(graph.task_id, TaskStatus.WAITING_RESOURCE)
            return TaskResult(
                task_id=graph.task_id,
                status=TaskStatus.WAITING_RESOURCE,
                metrics={"submission_id": record["id"], "submission_state": record["state"]},
                error=f"{type(exc).__name__}: {exc}",
            )
        if not isinstance(result, dict) or result.get("status") not in {
            status.value for status in TaskStatus
        }:
            await self._save_record(
                {**record, "state": "uncertain", "error": "Invalid workflow response"}
            )
            if self._metadata is not None:
                await self._transition_task(graph.task_id, TaskStatus.WAITING_RESOURCE)
            return TaskResult(
                task_id=graph.task_id,
                status=TaskStatus.WAITING_RESOURCE,
                error=str(result),
            )
        try:
            status = TaskStatus(result.get("status", "failed"))
        except ValueError:
            status = TaskStatus.FAILED
        waiting_reason = None
        if status == TaskStatus.WAITING_RESOURCE:
            # Preserve only a narrowly recognized admission diagnostic from
            # the exact waiting node. Arbitrary model output is not a durable
            # retry authorization or proof that no side effect occurred.
            node_results = result.get("nodes")
            for node_result in node_results if isinstance(node_results, list) else []:
                if not isinstance(node_result, dict):
                    continue
                output = node_result.get("output")
                if (node_result.get("node_id") == result.get("node_id")
                        and isinstance(output, dict) and output.get("waiting") is True
                        and output.get("waiting_kind") == "resource"
                        and output.get("waiting_reason") == "isolated_worker_not_assembled"):
                    waiting_reason = "isolated_worker_not_assembled"
                    break
        await self._save_record(
            {
                **record,
                "state": "resolved",
                "result": {
                    "status": status.value,
                    "error": result.get("error"),
                    "node_id": result.get("node_id"),
                    **({"waiting_reason": waiting_reason} if waiting_reason else {}),
                },
            }
        )
        if self._metadata is not None:
            await self._transition_task(graph.task_id, status)
        return TaskResult(
            task_id=graph.task_id,
            status=status,
            error=result.get("error"),
        )

    async def _save_record(self, record: dict[str, Any]) -> None:
        if self._metadata is not None:
            await self._metadata.update_task_submission(record)
        elif self._records.get(record["id"], {}).get("state") != "resolved":
            self._records[record["id"]] = record

    async def _transition_task(self, task_id: str, target: TaskStatus) -> None:
        """Update task status without raising on already-settled states."""
        current = await self._metadata.get_task(task_id)
        if current is None:
            return
        try:
            TaskStateMachine.validate(current.status, target)
        except ValueError:
            return
        await self._metadata.update_task_status(task_id, target)


async def probe_temporal(config: TemporalConfig | None = None) -> dict[str, Any]:
    """Probe Temporal without requiring the Temporal CLI.

    The returned report is intentionally plain data so the CLI can format it
    for humans and tests can assert on it without touching the server.
    """
    from temporalio.api.enums.v1 import TaskQueueKind, TaskQueueType
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import (
        DescribeNamespaceRequest,
        DescribeTaskQueueRequest,
        GetSystemInfoRequest,
    )
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode

    config = config or TemporalConfig.from_env()
    report: dict[str, Any] = {
        "host": config.host,
        "namespace": config.namespace,
        "task_queue": config.task_queue,
        "reachable": False,
        "server_version": None,
        "namespace_found": None,
        "workers": 0,
        "error": None,
    }
    try:
        client = await Client.connect(config.host, namespace=config.namespace, lazy=True)
        system = await client.workflow_service.get_system_info(GetSystemInfoRequest())
        report["reachable"] = True
        report["server_version"] = system.server_version
        try:
            await client.workflow_service.describe_namespace(
                DescribeNamespaceRequest(namespace=config.namespace)
            )
            report["namespace_found"] = True
        except RPCError as exc:
            report["namespace_found"] = exc.status != RPCStatusCode.NOT_FOUND
            if exc.status != RPCStatusCode.NOT_FOUND:
                report["error"] = str(exc)
        if report["namespace_found"]:
            try:
                queue = await client.workflow_service.describe_task_queue(
                    DescribeTaskQueueRequest(
                        namespace=config.namespace,
                        task_queue=TaskQueue(
                            name=config.task_queue,
                            kind=TaskQueueKind.TASK_QUEUE_KIND_NORMAL,
                        ),
                        task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
                        include_task_queue_status=True,
                        report_pollers=True,
                    )
                )
                report["workers"] = len(queue.pollers)
            except RPCError as exc:
                report["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - probe must never raise
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report
