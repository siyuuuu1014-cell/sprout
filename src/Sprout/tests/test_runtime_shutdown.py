"""Shutdown owns local workers without changing business task status."""

import asyncio

import pytest

from Sprout.task.models import Task
from Sprout.tests.conftest import build_runtime


async def test_runtime_stop_joins_idle_worker():
    runtime, _ = build_runtime()
    await runtime.start()
    runtime.start_worker()
    worker = runtime._worker_task
    await asyncio.sleep(0)
    try:
        await runtime.stop()
        assert worker.done(), "Runtime.stop leaked the task queue worker"
        assert runtime._worker_task is None
    finally:
        await runtime.stop_worker()


async def test_worker_can_restart_and_concurrent_stop_is_idempotent(monkeypatch):
    runtime, _ = build_runtime()
    runtime.start_worker()
    await asyncio.sleep(0)
    original = runtime._worker_task
    await asyncio.gather(runtime.stop_worker(), runtime.stop_worker())
    assert original.done()
    assert runtime._worker_task is None
    finished = asyncio.Event()

    async def execute(task):
        finished.set()

    monkeypatch.setattr(runtime, "execute", execute)
    await runtime.submit_task(Task(workspace_id="ws", instruction="after restart"))
    runtime.start_worker()
    try:
        assert runtime._worker_task is not original
        await asyncio.wait_for(finished.wait(), 1)
    finally:
        await runtime.stop_worker()


async def test_shutdown_joins_execution_before_file_stores_close(tmp_path, monkeypatch):
    from Sprout.config.loader import default_settings
    from Sprout.runtime.factory import create_runtime
    from Sprout.task.models import TaskStatus

    settings = default_settings()
    settings.model.provider = "echo"
    settings.storage.operational = f"sqlite:///{(tmp_path / 'op.db').as_posix()}"
    settings.storage.metadata = f"sqlite:///{(tmp_path / 'meta.db').as_posix()}"
    settings.storage.session = f"sqlite:///{(tmp_path / 'session.db').as_posix()}"
    settings.storage.knowledge = "memory://"
    settings.storage.observations.enabled = False
    settings.storage.trajectory_dir = str(tmp_path / "trajectory")
    settings.storage.blobs_dir = str(tmp_path / "blobs")
    settings.security.audit.path = str(tmp_path / "audit.jsonl")
    runtime = create_runtime(settings)
    await runtime.start()
    task = Task(workspace_id="ws", instruction="unfinished", status=TaskStatus.RUNNING)
    await runtime.storage.metadata.save_task(task)
    entered, flushed = asyncio.Event(), asyncio.Event()

    async def execute(queued):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            # A cancelled local consumer can still persist its final evidence.
            await runtime.storage.metadata.save_task(queued)
            flushed.set()

    monkeypatch.setattr(runtime, "execute", execute)
    await runtime.submit_task(task)
    runtime.start_worker()
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(runtime.stop(), 2)
    assert flushed.is_set()
    assert runtime._worker_task is None
    reloaded = create_runtime(settings)
    try:
        stored = await reloaded.storage.metadata.get_task(task.id)
        assert stored.status is TaskStatus.RUNNING
    finally:
        await reloaded.storage.close()


async def test_background_writes_finish_before_final_outbox_flush(monkeypatch):
    runtime, _ = build_runtime()
    await runtime.start()
    calls = []

    async def write():
        await asyncio.sleep(0)
        calls.append("write")

    async def flush():
        calls.append("outbox")

    monkeypatch.setattr(runtime, "_stop_outbox_worker", flush)
    runtime._background_tasks.add(asyncio.create_task(write()))
    await runtime.stop()
    assert calls == ["write", "outbox"]


async def test_failed_worker_still_flushes_and_closes_stores(monkeypatch):
    runtime, _ = build_runtime()
    await runtime.start()
    calls = []

    async def failed():
        raise ValueError("worker failed")

    async def flush():
        calls.append("outbox")

    async def close():
        calls.append("storage")

    monkeypatch.setattr(runtime, "_stop_outbox_worker", flush)
    monkeypatch.setattr(type(runtime.storage), "close", lambda self: close())
    worker = asyncio.create_task(failed())
    runtime._worker_task = worker
    await asyncio.sleep(0)
    with pytest.raises(ValueError, match="worker failed"):
        await runtime.stop()
    assert calls == ["outbox", "storage"]
    assert runtime._worker_task is None


async def test_runtime_stop_joins_worker_started_without_runtime_start():
    runtime, _ = build_runtime()
    runtime.start_worker()
    worker = runtime._worker_task
    await asyncio.sleep(0)
    try:
        await runtime.stop()
        assert worker.done(), "A manually started worker leaked through early return"
    finally:
        await runtime.stop_worker()


async def test_worker_does_not_dequeue_again_when_execution_consumes_cancellation(monkeypatch):
    runtime, _ = build_runtime()
    entered = asyncio.Event()
    finished = asyncio.Event()
    calls = []

    async def execute(task):
        calls.append(task.id)
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Some external adapters consume cancellation and return a result.
            finished.set()
            return None

    monkeypatch.setattr(runtime, "execute", execute)
    first = Task(workspace_id="ws", instruction="first")
    second = Task(workspace_id="ws", instruction="second")
    await runtime.submit_task(first)
    await runtime.submit_task(second)
    runtime.start_worker()
    await asyncio.wait_for(entered.wait(), 1)
    stopping = asyncio.create_task(runtime.stop_worker())
    try:
        await asyncio.wait_for(finished.wait(), 1)
        done, _ = await asyncio.wait({stopping}, timeout=0.1)
        assert stopping in done, "The cancelled worker dequeued again and blocked shutdown"
        assert calls == [first.id]
        assert runtime._task_queue.qsize() == 1
        assert (await runtime._task_queue.get()).id == second.id
        runtime._task_queue.task_done()
    finally:
        # Do not let the broken implementation hang pytest's event-loop cleanup.
        if runtime._worker_task is not None:
            runtime._worker_task.cancel()
        try:
            await asyncio.wait_for(stopping, 1)
        except (TimeoutError, asyncio.CancelledError):
            pass
        await runtime.stop_worker()
