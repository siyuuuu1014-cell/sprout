"""Temporal-backed task queue used by the runtime."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from Sprout.task.models import Task


class TaskQueueBackend(Protocol):
    async def put(self, task: Task) -> None: ...
    async def get(self) -> Task: ...
    def task_done(self) -> None: ...
    def qsize(self) -> int: ...
    def empty(self) -> bool: ...
    def __aiter__(self) -> AsyncIterator[Task]: ...


class TaskQueue(TaskQueueBackend):
    """FIFO queue for tasks before workspace execution."""

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[Task] = asyncio.Queue(maxsize=maxsize)

    async def put(self, task: Task) -> None:
        await self._queue.put(task)

    async def get(self) -> Task:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    async def __aiter__(self) -> AsyncIterator[Task]:
        while True:
            task = await self.get()
            yield task


class TemporalTaskQueue(TaskQueue):
    """Submission adapter; never puts remote tasks into the local execution FIFO."""

    def __init__(self) -> None:
        super().__init__()
        self._submit: Callable[[Task], Awaitable[object]] | None = None

    def bind(self, submit: Callable[[Task], Awaitable[object]]) -> None:
        self._submit = submit

    async def put(self, task: Task) -> None:
        if self._submit is None:
            raise RuntimeError("Temporal queue requires a durable Temporal runtime")
        await self._submit(task)


def create_task_queue() -> TaskQueueBackend:
    """Return the configured task-queue backend.

    Temporal is the durable production queue, but it is opt-in via
    ``TEMPORAL_HOST``; the offline/local default is a plain in-process FIFO so
    task submission never blocks waiting for a Temporal worker.
    """
    if os.getenv("TEMPORAL_HOST"):
        return TemporalTaskQueue()
    return TaskQueue()
