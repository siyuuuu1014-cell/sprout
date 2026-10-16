"""Foreground execution of one already-persisted isolated coding experiment."""

import asyncio
import sys
from contextlib import asynccontextmanager

from Sprout.execution.coding_dispatch import IsolatedNodeDispatcher
from Sprout.orchestration.isolated_worker import IsolatedCodingWorker, experiment_task_queue
from Sprout.orchestration.isolated_worker_lifecycle import run_owned_workers
from Sprout.orchestration.terminal.temporal import TemporalConfig, TemporalOrchestratorBackend


def _reject_legacy_mapping(**kwargs):
    raise ValueError("Isolated coding requires dedicated plan-bound command sessions")


def assemble_coding_worker(runtime, task, *, host, namespace):
    """Shared assembly only; never submit, start generic Runtime or grant approval."""
    if not host or not host.strip() or not namespace or not namespace.strip():
        raise ValueError("Explicit Temporal host and namespace required")
    if getattr(runtime, "_started", False):
        raise ValueError("Unstarted experiment Runtime required")
    from Sprout.evolution.replay_plan import verify_plan

    payload = verify_plan(task.metadata.get("replay_plan"))
    runtime._isolated_coding_image = payload["coding_profile"]["image"]
    runtime._isolated_coding_dispatch = IsolatedNodeDispatcher(
        runtime.storage.metadata, runtime, _reject_legacy_mapping)
    config = TemporalConfig(host=host, namespace=namespace, task_queue=experiment_task_queue(task))
    worker = IsolatedCodingWorker(runtime, task, config)
    return worker, config


@asynccontextmanager
async def coding_execution(runtime, task, *, host, namespace, resume=False):
    """Keep worker/storage alive through execution and subsequent measurements.

    This embedded convenience scope still owns its worker. Use coding-worker
    for an independent foreground lifecycle, not tied to a client's wait.
    """
    worker, config = assemble_coding_worker(runtime, task, host=host, namespace=namespace)
    runtime._orchestrator_backend = TemporalOrchestratorBackend(
        config, metadata=runtime.storage.metadata)
    serving = asyncio.create_task(run_owned_workers(worker))

    async def execute():
        submitting = asyncio.create_task(
            runtime.resume_task(task) if resume else runtime.execute(task))
        try:
            done, _ = await asyncio.wait((serving, submitting),
                                         return_when=asyncio.FIRST_COMPLETED)
            if serving in done:
                serving.result()
                raise RuntimeError("Isolated coding worker stopped before task result")
            return submitting.result()
        finally:
            if not submitting.done():
                submitting.cancel()
            await asyncio.gather(submitting, return_exceptions=True)

    try:
        yield execute
    finally:
        failed_before_exit = serving.done()
        unwinding = sys.exc_info()[0] is not None
        if not serving.done():
            serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
        if failed_before_exit and not unwinding:
            serving.result()
            raise RuntimeError("Isolated coding worker exited unexpectedly")
