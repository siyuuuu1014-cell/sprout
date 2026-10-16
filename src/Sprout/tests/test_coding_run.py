import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.evolution.coding_run import coding_execution


def assembly(monkeypatch, execute):
    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=object()),
                              execute=execute, _started=False)
    task = SimpleNamespace(metadata={"replay_plan": {}}, id="experiment")
    config = []
    monkeypatch.setattr("Sprout.evolution.replay_plan.verify_plan",
                        lambda plan: {"coding_profile": {"image": "fixed-image"}})
    monkeypatch.setattr("Sprout.evolution.coding_run.experiment_task_queue",
                        lambda task: "task-owned-queue")

    def worker(runtime, task, route):
        config.append(route)
        return object()

    monkeypatch.setattr("Sprout.evolution.coding_run.IsolatedCodingWorker", worker)
    return runtime, task, config


async def test_foreground_worker_stays_alive_until_caller_finishes_storage_use(monkeypatch):
    runtime, task, routes = assembly(monkeypatch, AsyncMock(return_value="waiting_approval"))
    stopped = asyncio.Event()

    async def serving(worker):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr("Sprout.evolution.coding_run.run_owned_workers", serving)
    async with coding_execution(
        runtime, task, host="explicit-host", namespace="explicit-ns",
    ) as run:
        assert await run() == "waiting_approval"
        assert not stopped.is_set()
        assert runtime._isolated_coding_image == "fixed-image"
        assert callable(runtime._isolated_coding_dispatch)
        assert routes[0].host == "explicit-host" and routes[0].namespace == "explicit-ns"
        assert routes[0].task_queue == "task-owned-queue"
    assert stopped.is_set()
    runtime.execute.assert_awaited_once_with(task)


async def test_worker_failure_cancels_and_joins_inflight_submission(monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def submit(task):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    runtime, task, _ = assembly(monkeypatch, submit)

    async def serving(worker):
        await started.wait()
        raise RuntimeError("explicit worker failure")

    monkeypatch.setattr("Sprout.evolution.coding_run.run_owned_workers", serving)
    with pytest.raises(RuntimeError, match="explicit worker failure"):
        async with coding_execution(runtime, task, host="host", namespace="ns") as run:
            await run()
    assert cancelled.is_set()


async def test_submission_failure_still_joins_worker(monkeypatch):
    runtime, task, _ = assembly(monkeypatch, AsyncMock(side_effect=ValueError("submission failed")))
    stopped = asyncio.Event()

    async def serving(worker):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr("Sprout.evolution.coding_run.run_owned_workers", serving)
    with pytest.raises(ValueError, match="submission failed"):
        async with coding_execution(runtime, task, host="host", namespace="ns") as run:
            await run()
    assert stopped.is_set()


async def test_worker_failure_after_execution_is_not_silently_swallowed(monkeypatch):
    runtime, task, _ = assembly(monkeypatch, AsyncMock(return_value="waiting_approval"))
    stop = asyncio.Event()

    async def serving(worker):
        await stop.wait()
        raise RuntimeError("worker lost during measurement")

    monkeypatch.setattr("Sprout.evolution.coding_run.run_owned_workers", serving)
    with pytest.raises(RuntimeError, match="worker lost during measurement"):
        async with coding_execution(runtime, task, host="host", namespace="ns") as run:
            assert await run() == "waiting_approval"
            stop.set()
            await asyncio.sleep(0)


@pytest.mark.parametrize("host,namespace", [(None, "ns"), ("host", None), (" ", "ns")])
async def test_no_default_route(host, namespace):
    with pytest.raises(ValueError, match="Explicit Temporal"):
        async with coding_execution(SimpleNamespace(), None, host=host, namespace=namespace):
            pytest.fail("Invalid route must not enter execution")
