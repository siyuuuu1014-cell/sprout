"""Submit a fresh persisted experiment without owning or waiting for a Worker."""

from dataclasses import asdict
from pathlib import Path

from Sprout.cli.experiment_runtime import experiment_runtime
from Sprout.evolution.coding_run import assemble_coding_worker
from Sprout.evolution.replay_plan import verify_plan
from Sprout.gateway.local_identity import creator_blockers
from Sprout.orchestration.terminal.temporal import TemporalOrchestratorBackend
from Sprout.storage.local.sqlite.commit_status import _regular
from Sprout.storage.local.sqlite.verification_report import recovery_facts
from Sprout.task.models import TaskStatus


async def submit_coding(database, task_id, *, settings, principal, host, namespace):
    database = _regular(database)
    facts = recovery_facts(database, task_id)
    payload = verify_plan(facts["metadata"]["replay_plan"])
    if (creator_blockers(payload.get("creator"), principal)
            or Path(payload["repository_root"]).resolve().parent != database.parent):
        raise ValueError("Creator-bound experiment database required")
    if not host or not host.strip() or not namespace or not namespace.strip():
        raise ValueError("Explicit original Temporal host and namespace required")
    async with experiment_runtime(database, task_id, settings=settings) as (runtime, task):
        records = await runtime.storage.metadata.list_task_submissions(task_id)
        if records:
            # Existing history is observation-only here, never a resume/retry authorization.
            if len(records) != 1:
                raise ValueError("Existing submission history requires explicit observation")
            record = records[0]
            if (record.get("host"), record.get("namespace")) != (host, namespace):
                raise ValueError("Existing submission route cannot be changed")
            return {"task_id": task_id, "status": task.status.value,
                    "submission_id": record["id"], "submission_state": record["state"],
                    "new_submission": False, "next_action": "Use coding-status; no automatic retry"}
        if task.status not in {TaskStatus.CREATED, TaskStatus.READY, TaskStatus.RUNNING}:
            raise ValueError("Fresh active experiment required; not a recovery entry")
        if await runtime.storage.metadata.list_execution_nodes(task_id):
            raise ValueError("Existing nodes require observation, not fresh submission")
        # Shared admission only; never worker.run or generic Runtime.start.
        _, config = assemble_coding_worker(runtime, task, host=host, namespace=namespace)
        runtime._orchestrator_backend = TemporalOrchestratorBackend(
            config, metadata=runtime.storage.metadata, wait_for_result=False)
        result = await runtime.execute(task)
        return {**asdict(result), "new_submission": True, "worker_started": False,
                "next_action": "Serve coding-worker separately; observe with coding-status"}
