"""Fail-closed coding command routing, independent of ordinary process execution."""

from typing import Protocol

from Sprout.execution.coding_container import CodingCommand
from Sprout.execution.models import ProcessResult


class CodingBackend(Protocol):
    async def execute(self, request: CodingCommand, *, node_id: str) -> ProcessResult: ...


class CodingExecutionRouter:
    """Only a runtime-installed backend may execute; task metadata grants nothing.

    The caller must hold the persisted node execution claim. This router never
    retries, selects a host fallback, or interprets timeout as rollback.
    """

    def __init__(self, backend: CodingBackend | None = None):
        self._backend = backend

    async def execute(self, request: CodingCommand, *, node_id: str) -> ProcessResult:
        if not node_id:
            raise ValueError("Coding execution requires an owning node")
        if self._backend is None:
            return ProcessResult(request.command, allowed=False,
                                 error="isolated_coding_backend_not_ready")
        # Backend exceptions/cancellation propagate to the owning node guard;
        # returning a fake failed exit code would erase unknown side effects.
        return await self._backend.execute(request, node_id=node_id)
