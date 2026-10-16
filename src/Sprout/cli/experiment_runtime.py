"""Shared assembly of an existing experiment; never start a generic Runtime."""

from contextlib import asynccontextmanager

from Sprout.evolution.candidate_binding import restore_candidate
from Sprout.evolution.isolated_replay import isolated_settings
from Sprout.evolution.replay_config import configuration_blockers
from Sprout.evolution.replay_plan import verify_plan
from Sprout.storage.local.sqlite.commit_status import _regular
from Sprout.storage.local.sqlite.verification_report import recovery_facts


@asynccontextmanager
async def experiment_runtime(database, task_id, *, settings, source_metadata=None):
    """Caller checks creator and accepted route before opening mutable stores."""
    from Sprout.runtime.factory import create_runtime

    facts = recovery_facts(database, task_id)
    payload = verify_plan(facts["metadata"]["replay_plan"])
    directory = database.parent
    for slot in ("operational", "metadata", "session", "context", "usage"):
        _regular(directory / (slot + ".db"))
    isolated = isolated_settings(directory, settings)
    if isolated.mcp.clients or configuration_blockers(payload.get("configuration"), isolated):
        raise ValueError("Original experiment configuration required; no automatic adaptation")
    runtime = create_runtime(isolated)
    try:
        runtime._isolated_coding_required = True
        runtime._isolated_coding_image = payload["coding_profile"]["image"]
        task = await runtime.storage.metadata.get_task(task_id)
        if task is None or task.metadata.get("replay_plan") != facts["metadata"]["replay_plan"]:
            raise ValueError("Experiment task changed during worker assembly")
        if payload.get("candidate") is not None:
            if source_metadata is not None:
                await restore_candidate(runtime, task, source_metadata)
            else:
                # Commit request/refresh/worker entry points use the original
                # config authority. Never start it or copy approvals/skills.
                source = create_runtime(settings)
                try:
                    await restore_candidate(runtime, task, source.storage.metadata)
                finally:
                    await source.storage.close()
        yield runtime, task
    finally:
        if getattr(runtime, "_isolated_lifecycle_owned", False) is not True:
            await runtime.storage.close()
