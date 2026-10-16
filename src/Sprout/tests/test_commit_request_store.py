import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.storage.local.memory.operational import MemoryOperationalStore
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.operational import SqliteOperationalStore


def record():
    now = datetime.now(UTC)
    review = {"task_id": "task", "proposal_id": "proposal", "proposal_status": "approved",
              "write_authorized": False, "contract": {"fixture": "review only"}}
    return {"id": "request", "task_id": "task", "proposal_id": "proposal", "creator": "owner",
            "review": review, "fingerprint": fingerprint(review), "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat()}


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        yield MemoryOperationalStore()
    else:
        operational = SqliteOperationalStore(SqliteDatabase(tmp_path / "audit.db"))
        try:
            yield operational
        finally:
            operational.close()


async def test_one_decision_atomically_creates_dispatch_intent(store):
    item = record()
    await store.save_commit_request(item)
    async def decide():
        return await store.decide_commit_request(item["id"], creator="owner",
            expected_fingerprint=item["fingerprint"], approved=True, reason="Explicit")
    assert sorted(await asyncio.gather(decide(), decide())) == [False, True]
    saved = await store.get_commit_request(item["id"])
    assert saved["state"] == "approved" and saved["dispatch_state"] == "waiting_schedule"
    assert saved["decision"]["by"] == "owner"
    assert await store.list_approvals() == []


@pytest.mark.parametrize("change", ["creator", "fingerprint", "expired"])
async def test_wrong_or_stale_decisions_create_no_dispatch(store, change):
    item = record()
    if change == "expired":
        now = datetime.now(UTC)
        item.update(created_at=(now - timedelta(hours=2)).isoformat(),
                    expires_at=(now - timedelta(hours=1)).isoformat())
    await store.save_commit_request(item)
    assert not await store.decide_commit_request(item["id"],
        creator="other" if change == "creator" else "owner",
        expected_fingerprint="changed" if change == "fingerprint" else item["fingerprint"],
        approved=True, reason="")
    saved = await store.get_commit_request(item["id"])
    assert saved["state"] == "pending" and saved["dispatch_state"] is None


async def test_rejection_does_not_dispatch_and_frozen_review_cannot_be_overwritten(store):
    item = record()
    await store.save_commit_request(item)
    assert await store.decide_commit_request(item["id"], creator="owner",
        expected_fingerprint=item["fingerprint"], approved=False, reason="Keep waiting")
    saved = await store.get_commit_request(item["id"])
    assert saved["state"] == "rejected" and saved["dispatch_state"] is None
    changed = deepcopy(item)
    changed["review"]["contract"] = {"different": True}
    changed["fingerprint"] = fingerprint(changed["review"])
    with pytest.raises(ValueError, match="different content"):
        await store.save_commit_request(changed)


async def test_sqlite_decision_survives_close_and_reopen(tmp_path):
    path = tmp_path / "audit.db"
    store = SqliteOperationalStore(SqliteDatabase(path))
    item = record()
    await store.save_commit_request(item)
    await store.decide_commit_request(item["id"], creator="owner",
        expected_fingerprint=item["fingerprint"], approved=True, reason="Explicit")
    store.close()
    reopened = SqliteOperationalStore(SqliteDatabase(path))
    try:
        saved = await reopened.get_commit_request(item["id"])
        assert saved["decision"]["approved"] is True
        assert saved["dispatch_state"] == "waiting_schedule"
    finally:
        reopened.close()


async def test_duplicate_request_never_renews_original_lifetime(store):
    item = record()
    await store.save_commit_request(item)
    repeated = {**item, "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}
    saved = await store.save_commit_request(repeated)
    assert saved["expires_at"] == item["expires_at"]
