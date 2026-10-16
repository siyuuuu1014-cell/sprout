import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.orchestration.isolated_worker import IsolatedCodingWorker, IsolatedCommitWorker
from Sprout.orchestration.isolated_worker_lifecycle import run_owned_workers


def worker(cls, queue, events):
    bound = object.__new__(cls)
    bound._task_id, bound._binding = "task", "binding"
    bound._config = SimpleNamespace(host="explicit", namespace="experiment", task_queue=queue)

    async def close():
        events.append("close:" + queue)

    bound._runtime = SimpleNamespace(_started=False, storage=SimpleNamespace(close=AsyncMock(
        side_effect=close)), start=AsyncMock(), stop=AsyncMock())

    async def run():
        try:
            events.append("run:" + queue)
            await asyncio.Event().wait()
        finally:
            events.append("join:" + queue)

    bound.run = run
    return bound


async def test_cancellation_joins_all_before_storage_close():
    events = []
    coding = worker(IsolatedCodingWorker, "coding", events)
    commit = worker(IsolatedCommitWorker, "commit", events)
    running = asyncio.create_task(run_owned_workers(coding, commit))
    while len(events) < 2:
        await asyncio.sleep(0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert set(events[2:4]) == {"join:coding", "join:commit"}
    assert set(events[4:]) == {"close:coding", "close:commit"}
    for bound in (coding, commit):
        bound._runtime.start.assert_not_called()
        bound._runtime.stop.assert_not_called()
        bound._runtime.storage.close.assert_awaited_once()
    with pytest.raises(ValueError, match="consumed"):
        await run_owned_workers(coding, commit)


async def test_failure_stops_sibling_and_preserves_original_error():
    events = []
    coding = worker(IsolatedCodingWorker, "coding", events)
    commit = worker(IsolatedCommitWorker, "commit", events)

    async def fail():
        raise OSError("worker unavailable")

    commit.run = fail
    coding._runtime.storage.close.side_effect = RuntimeError("close failed")
    with pytest.raises(OSError, match="worker unavailable"):
        await run_owned_workers(coding, commit)
    assert "join:coding" in events
    commit._runtime.storage.close.assert_awaited_once()


@pytest.mark.parametrize("invalid", ["scope", "queue", "started", "runtime"])
async def test_invalid_assembly_does_not_take_ownership(invalid):
    events = []
    coding = worker(IsolatedCodingWorker, "coding", events)
    commit = worker(IsolatedCommitWorker, "commit", events)
    if invalid == "scope":
        commit._binding = "other"
    elif invalid == "queue":
        commit._config.task_queue = "coding"
    elif invalid == "started":
        commit._runtime._started = True
    else:
        commit._runtime = coding._runtime
    with pytest.raises(ValueError):
        await run_owned_workers(coding, commit)
    assert events == []
    assert not hasattr(coding._runtime, "_isolated_lifecycle_owned")
