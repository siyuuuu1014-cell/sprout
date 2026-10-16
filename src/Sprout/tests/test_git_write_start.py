import asyncio
from dataclasses import replace

import pytest

from Sprout.orchestration.models import NodeStatus
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import TaskStatus
from Sprout.tests.test_git_transaction_store import fixture


async def test_start_once_across_connections_and_restart(tmp_path):
    path, store, _, record, snapshot = await fixture(tmp_path)
    other = open_metadata_store(str(path))
    try:
        assert await store.claim_git_transaction(record, snapshot)
        starts = await asyncio.gather(
            store.begin_git_commit_write(record["id"], "owner"),
            other.begin_git_commit_write(record["id"], "owner"))
        assert sorted(starts) == [False, True]
        persisted = await store.get_git_transaction(record["id"])
        assert persisted["state"] == "active"
        assert persisted["write_started"]["owner"] == "owner"
        assert not await store.begin_git_commit_write(record["id"], "owner")
    finally:
        store.close()
        other.close()
    reopened = open_metadata_store(str(path))
    try:
        assert not await reopened.begin_git_commit_write(record["id"], "owner")
        assert await reopened.finish_git_transaction(record["id"], "owner", "uncertain", {})
        assert not await reopened.begin_git_commit_write(record["id"], "owner")
    finally:
        reopened.close()


@pytest.mark.parametrize("change", ["owner", "proposal", "cancelled", "node", "uncertain"])
async def test_stale_or_unknown_owner_cannot_start_write(tmp_path, change):
    _, store, proposal, record, snapshot = await fixture(tmp_path)
    try:
        assert await store.claim_git_transaction(record, snapshot)
        owner = "owner"
        if change == "owner":
            owner = "foreign"
        elif change == "proposal":
            await store.save_change_proposal(replace(proposal, metadata={"drift": True}))
        elif change == "cancelled":
            await store.update_task_status(record["task_id"], TaskStatus.CANCELLED)
        elif change == "node":
            node = await store.get_execution_node(record["node_id"])
            assert await store.finish_execution_node(
                replace(node, status=NodeStatus.COMPLETED), "owner", "completed")
        else:
            await store.finish_git_transaction(record["id"], "owner", "uncertain", {})
        assert not await store.begin_git_commit_write(record["id"], owner)
        assert "write_started" not in await store.get_git_transaction(record["id"])
    finally:
        store.close()


@pytest.mark.parametrize("field", ["state", "result", "proposal_snapshot", "write_started"])
async def test_claim_cannot_inject_storage_owned_lifecycle_fields(tmp_path, field):
    _, store, _, record, snapshot = await fixture(tmp_path)
    try:
        record[field] = "injected"
        with pytest.raises(ValueError, match="storage-owned"):
            await store.claim_git_transaction(record, snapshot)
    finally:
        store.close()
