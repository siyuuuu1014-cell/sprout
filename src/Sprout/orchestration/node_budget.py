"""Owner-local active execution clock; human decisions are not execution time."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar

_CURRENT = ContextVar("node_execution_budget", default=None)


class NodeExecutionBudget:
    def __init__(self, node):
        self.node = node
        self.owner = asyncio.current_task()
        self.remaining = node.budget.timeout_seconds
        self.timer = None
        self.paused = False

    async def __aenter__(self):
        self.timer = asyncio.timeout(self.remaining)
        await self.timer.__aenter__()
        self.token = _CURRENT.set(self)
        return self

    async def __aexit__(self, *exc):
        _CURRENT.reset(self.token)
        return await self.timer.__aexit__(*exc)

    @contextmanager
    def approval_wait(self):
        if self.owner is not asyncio.current_task() or self.paused or self.timer.expired():
            raise PermissionError("approval_wait_budget_owner_unavailable")
        self.remaining = self.timer.when() - asyncio.get_running_loop().time()
        if self.remaining <= 0:
            raise TimeoutError("Node execution budget exhausted before approval wait")
        self.timer.reschedule(None)
        self.paused = True
        try:
            yield
        finally:
            self.paused = False
            self.timer.reschedule(asyncio.get_running_loop().time() + self.remaining)


def current_node_budget(node):
    clock = _CURRENT.get()
    if (clock is not None and clock.owner is asyncio.current_task()
            and clock.node.id == node.id and clock.node.task_id == node.task_id
            and clock.node.budget == node.budget
            and clock.node.metadata.get("execution_guard", {}).get("owner")
            == node.metadata.get("execution_guard", {}).get("owner")):
        return clock
    return None


def active_seconds_remaining(node):
    clock = current_node_budget(node)
    if clock is None:
        return None
    if clock.paused:
        return clock.remaining
    return max(0, clock.timer.when() - asyncio.get_running_loop().time())
