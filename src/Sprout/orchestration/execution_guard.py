"""Non-expiring activity ownership; uncertain side effects are never replayed."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from uuid import uuid4

from Sprout.orchestration.models import ExecutionNode, NodeStatus
from Sprout.storage.contracts.metadata import MetadataStore


def node_result(node: ExecutionNode) -> dict[str, Any]:
    return {
        "node_id": node.id,
        "type": node.type.value,
        "status": node.status.value,
        "output": node.metadata.get("output", {}),
    }


def unconfirmed_result(node: ExecutionNode, error: str | None = None) -> dict[str, Any]:
    """A duplicate/abandoned owner is not evidence that its work never happened."""
    return {
        "node_id": node.id,
        "type": node.type.value,
        "status": "waiting",
        "output": {
            "waiting": True,
            "waiting_kind": "resource",
            "reason": "execution_result_unconfirmed",
            "error": error,
        },
    }


class NodeExecutionGuard:
    """Use SQLite node ownership rather than process locks or expiring leases."""

    def __init__(self, metadata: MetadataStore) -> None:
        self._metadata = metadata

    async def execute(
        self,
        node: ExecutionNode,
        execute: Callable[[ExecutionNode], Awaitable[Any]],
        *,
        resume_key: str = "",
    ) -> dict[str, Any]:
        current = await self._metadata.get_execution_node(node.id)
        cached = self._cached(node, current, resume_key)
        if cached is not None:
            return cached
        if node.status not in {NodeStatus.PENDING, NodeStatus.READY, NodeStatus.WAITING}:
            return unconfirmed_result(node)
        owner = str(uuid4())
        if not await self._metadata.claim_execution_node(node, owner, resume_key):
            current = await self._metadata.get_execution_node(node.id)
            return self._cached(node, current, resume_key) or unconfirmed_result(node)
        running = await self._metadata.get_execution_node(node.id)
        if running is None or running.metadata.get("execution_guard", {}).get("owner") != owner:
            return unconfirmed_result(node)
        try:
            output = await execute(running)
        except (Exception, asyncio.CancelledError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            output = unconfirmed_result(running, error)["output"]
            waiting = replace(
                running,
                status=NodeStatus.WAITING,
                metadata={**running.metadata, "output": output, "error": error},
            )
            await self._metadata.finish_execution_node(waiting, owner, "uncertain")
            if isinstance(exc, asyncio.CancelledError):
                raise
            return node_result(waiting)
        is_waiting = isinstance(output, dict) and bool(output.get("waiting"))
        state = (
            ("uncertain" if output.get("waiting_kind") == "resource" else "waiting")
            if is_waiting
            else "completed"
        )
        settled = replace(
            running,
            status=NodeStatus.WAITING if is_waiting else NodeStatus.COMPLETED,
            metadata={**running.metadata, "output": output},
        )
        if not await self._metadata.finish_execution_node(settled, owner, state):
            return unconfirmed_result(running)
        return node_result(settled)

    @staticmethod
    def _cached(
        requested: ExecutionNode,
        current: ExecutionNode | None,
        resume_key: str,
    ) -> dict[str, Any] | None:
        if current is None:
            return None
        if current.task_id != requested.task_id or current.type != requested.type:
            raise ValueError("Execution node identity does not match persisted authority")
        if current.status in {NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.CANCELLED}:
            return node_result(current)
        guard = current.metadata.get("execution_guard", {})
        if current.status in {NodeStatus.RUNNING, NodeStatus.FAILED} or guard.get("state") in {
            "active",
            "uncertain",
        }:
            return unconfirmed_result(current, current.metadata.get("error"))
        if current.status == NodeStatus.WAITING and (
            not resume_key or guard.get("resume_key") == resume_key
        ):
            output = current.metadata.get("output")
            if isinstance(output, dict) and output.get("waiting"):
                return node_result(current)
            return unconfirmed_result(current)
        return None
