"""Foreground coding worker only; submission and client waits are separate owners."""

from Sprout.cli.commit_submission import _route
from Sprout.cli.experiment_runtime import experiment_runtime
from Sprout.evolution.coding_run import assemble_coding_worker
from Sprout.orchestration.isolated_worker_lifecycle import run_owned_workers
from Sprout.storage.local.sqlite.commit_status import _regular
from Sprout.task.models import TaskStatus


async def serve_coding_worker(database, task_id, *, settings, principal):
    database = _regular(database)
    # Resolve the creator-bound accepted route; no command-line host/queue override.
    host, namespace, _ = _route(database, task_id, principal)
    async with experiment_runtime(database, task_id, settings=settings) as (runtime, task):
        if task.status not in {TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL,
                               TaskStatus.WAITING_RESOURCE}:
            raise ValueError("Active existing experiment required for coding worker")
        worker, _ = assemble_coding_worker(runtime, task, host=host, namespace=namespace)
        # No execute/resume/approval calls. Worker lives until stopped by its operator.
        await run_owned_workers(worker)
