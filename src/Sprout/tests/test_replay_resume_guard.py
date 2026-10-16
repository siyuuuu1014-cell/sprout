import asyncio

import pytest

from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus


async def setup_store(tmp_path):
    store = open_metadata_store(str(tmp_path / "metadata.db"))
    task = Task(status=TaskStatus.WAITING_RESOURCE, metadata={
        "execution_mode": "code", "replay_stage": "image_preflight", "replay_plan": {
            "fingerprint": "frozen", "payload": {"schema_version": 4,
                "creator": {"source": "cli", "authenticated": True, "user_id": "creator"},
                "configuration": {"schema_version": 1},
                "runtime_snapshot": {"schema_version": 2}},
        },
    })
    await store.save_task(task)
    return store, task


async def test_resume_single_owner_persists_after_reopen(tmp_path):
    store, task = await setup_store(tmp_path)
    other = open_metadata_store(str(tmp_path / "metadata.db"))
    try:
        attempts = [{"task_id": task.id, "owner": owner, "plan_fingerprint": "frozen",
                     "requested_by": "creator"}
                    for owner in ("first", "second")]
        winners = await asyncio.gather(store.claim_replay_resume(attempts[0]),
                                       other.claim_replay_resume(attempts[1]))
        assert sum(winners) == 1
        winner = attempts[winners.index(True)]
        assert not await store.finish_replay_resume(task.id, "wrong", "completed", {})
        assert await store.finish_replay_resume(task.id, winner["owner"], "uncertain", {})
        assert not await store.finish_replay_resume(task.id, winner["owner"], "completed", {})
    finally:
        store.close()
        other.close()
    reopened = open_metadata_store(str(tmp_path / "metadata.db"))
    try:
        assert (await reopened.get_replay_resume(task.id))["state"] == "uncertain"
        assert not await reopened.claim_replay_resume(attempts[0])
    finally:
        reopened.close()


@pytest.mark.parametrize("mode,status,fingerprint", [
    ("read_only", TaskStatus.WAITING_RESOURCE, "frozen"),
    ("code", TaskStatus.COMPLETED, "frozen"),
    ("code", TaskStatus.WAITING_RESOURCE, "changed"),
])
async def test_resume_conditions_are_atomic(tmp_path, mode, status, fingerprint):
    store, task = await setup_store(tmp_path)
    try:
        from dataclasses import replace

        await store.save_task(replace(task, status=status,
                                      metadata={**task.metadata, "execution_mode": mode}))
        assert not await store.claim_replay_resume({
            "task_id": task.id, "owner": "owner", "plan_fingerprint": fingerprint,
            "requested_by": "creator",
        })
        assert await store.get_replay_resume(task.id) is None
    finally:
        store.close()


async def test_existing_node_blocks_claim_and_task_save_cannot_release_owner(tmp_path):
    from dataclasses import replace

    from Sprout.orchestration.models import ExecutionNode

    store, task = await setup_store(tmp_path)
    record = {"task_id": task.id, "owner": "owner", "plan_fingerprint": "frozen",
              "requested_by": "creator"}
    try:
        assert await store.claim_replay_resume(record)
        await store.save_task(task)
        assert not await store.claim_replay_resume(record)
        second = replace(task, id="second")
        await store.save_task(second)
        await store.save_execution_node(ExecutionNode(task_id=second.id))
        assert not await store.claim_replay_resume({**record, "task_id": second.id})
    finally:
        store.close()


async def test_legacy_plan_cannot_claim_resume(tmp_path):
    from dataclasses import replace

    store, task = await setup_store(tmp_path)
    try:
        await store.save_task(replace(task, metadata={"execution_mode": "code", "replay_plan": {
            "fingerprint": "frozen", "payload": {"schema_version": 1},
        }}))
        assert not await store.claim_replay_resume({
            "task_id": task.id, "owner": "owner", "plan_fingerprint": "frozen",
        })
    finally:
        store.close()


async def test_other_operator_cannot_claim_resume(tmp_path):
    store, task = await setup_store(tmp_path)
    try:
        assert not await store.claim_replay_resume({
            "task_id": task.id, "owner": "owner", "plan_fingerprint": "frozen",
            "requested_by": "another-account",
        })
    finally:
        store.close()
