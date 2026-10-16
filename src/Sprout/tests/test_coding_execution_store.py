import asyncio
from dataclasses import replace

import pytest

from Sprout.orchestration.models import ExecutionNode, NodeStatus
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus


async def fixture(tmp_path, mode="code"):
    path = tmp_path / "metadata.db"
    store = open_metadata_store(str(path))
    task = Task(status=TaskStatus.RUNNING, metadata={"execution_mode": mode})
    await store.save_task(task)
    node = ExecutionNode(task_id=task.id)
    await store.save_execution_node(node)
    assert await store.claim_execution_node(node, "owner", "")
    record = {"id": "command", "task_id": task.id, "node_id": node.id,
              "owner": "owner", "signature": "signature"}
    if mode == "code":
        await store.ensure_coding_intent(record)
    return path, store, record


async def test_atomic_single_claim_and_restart_retains_uncertain(tmp_path):
    path, store, record = await fixture(tmp_path)
    other = open_metadata_store(str(path))
    try:
        claims = await asyncio.gather(store.claim_coding_execution(record),
                                      other.claim_coding_execution(record))
        assert sorted(claims) == [False, True]
        assert not await store.finish_coding_execution("command", "wrong", "completed", {})
        assert await store.finish_coding_execution("command", "owner", "uncertain", {})
        assert not await other.claim_coding_execution(record)
    finally:
        store.close()
        other.close()
    reopened = open_metadata_store(str(path))
    try:
        assert (await reopened.get_coding_execution("command"))["state"] == "uncertain"
        assert not await reopened.claim_coding_execution(record)
        assert not await reopened.finish_coding_execution("command", "owner", "completed", {})
    finally:
        reopened.close()


@pytest.mark.parametrize("change", ["owner", "task", "mode", "cancelled"])
async def test_invalid_authority_cannot_claim(tmp_path, change):
    _, store, record = await fixture(tmp_path, mode="read_only" if change == "mode" else "code")
    try:
        if change == "owner":
            record["owner"] = "wrong"
        elif change == "task":
            record["task_id"] = "other"
        elif change == "cancelled":
            await store.update_task_status(record["task_id"], TaskStatus.CANCELLED)
        assert not await store.claim_coding_execution(record)
        assert await store.get_coding_execution("command") is None
    finally:
        store.close()


async def test_frozen_intent_cannot_be_replaced_after_restart(tmp_path):
    path, store, record = await fixture(tmp_path)
    store.close()
    reopened = open_metadata_store(str(path))
    try:
        assert await reopened.ensure_coding_intent(record) == record
        with pytest.raises(ValueError, match="changed"):
            await reopened.ensure_coding_intent({**record, "baseline": {"app.py": "new"}})
    finally:
        reopened.close()


async def test_unknown_result_blocks_new_slot_and_preexisting_slot(tmp_path):
    _, store, record = await fixture(tmp_path)
    other = {**record, "id": "other-slot"}
    try:
        await store.ensure_coding_intent(other)
        assert await store.claim_coding_execution(record)
        assert not await store.claim_coding_execution(other)
        assert await store.finish_coding_execution("command", "owner", "uncertain", {})
        assert not await store.claim_coding_execution(other)
        with pytest.raises(ValueError, match="unknown effects"):
            await store.ensure_coding_intent({**record, "id": "new-slot"})
    finally:
        store.close()


async def test_claim_requires_exact_frozen_fingerprint(tmp_path):
    _, store, record = await fixture(tmp_path)
    try:
        assert not await store.claim_coding_execution({**record, "signature": "different"})
        assert not await store.claim_coding_execution({**record, "id": "unfrozen"})
    finally:
        store.close()


async def test_known_unexecuted_handoff_revalidates_new_owner_without_changing_inputs(tmp_path):
    _, store, record = await fixture(tmp_path)
    try:
        running = await store.get_execution_node(record["node_id"])
        waiting = replace(running, status=NodeStatus.WAITING, metadata={
            **running.metadata, "output": {"waiting": True, "waiting_kind": "approval"}})
        assert await store.finish_execution_node(waiting, "owner", "waiting")
        current = await store.get_execution_node(record["node_id"])
        assert await store.claim_execution_node(current, "new-owner", "approved")
        with pytest.raises(ValueError, match="authority"):
            await store.ensure_coding_intent(record)
        new_record = {**record, "owner": "new-owner"}
        original = await store.ensure_coding_intent(new_record)
        assert original["owner"] == "owner"  # original audit record is never rewritten
        assert await store.claim_coding_execution(new_record)
    finally:
        store.close()


async def test_broker_proven_no_launch_can_reclaim_same_request_and_preserve_evidence(tmp_path):
    path, store, record = await fixture(tmp_path)
    try:
        assert await store.claim_coding_execution(record)
        proof = {"allowed": False, "started": False, "exit_code": None, "approval_id": "pending"}
        assert await store.finish_coding_execution("command", "owner", "not_started", proof)
    finally:
        store.close()
    reopened = open_metadata_store(str(path))
    try:
        assert not await reopened.claim_coding_execution({**record, "signature": "changed"})
        assert await reopened.claim_coding_execution(record)
        row = await reopened.get_coding_execution("command")
        assert row["state"] == "active" and row["result"] is None
        assert row["no_launch_history"] == [{"owner": "owner", "result": proof}]
        assert not await reopened.claim_coding_execution(record)
    finally:
        reopened.close()


@pytest.mark.parametrize("proof", [{"allowed": False},
    {"allowed": False, "started": True}, {"allowed": True, "started": False},
    {"allowed": False, "started": False, "exit_code": 0}])
async def test_unknown_or_contradictory_result_cannot_settle_as_no_launch(tmp_path, proof):
    _, store, record = await fixture(tmp_path)
    try:
        assert await store.claim_coding_execution(record)
        with pytest.raises(ValueError, match="no-launch"):
            await store.finish_coding_execution("command", "owner", "not_started", proof)
        assert (await store.get_coding_execution("command"))["state"] == "active"
    finally:
        store.close()
