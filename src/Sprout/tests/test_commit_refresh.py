from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.gateway.identity import Principal
from Sprout.runtime.experiment_commits import ExperimentCommitService
from Sprout.storage.local.memory.operational import MemoryOperationalStore
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.operational import SqliteOperationalStore
from Sprout.tests.test_commit_request_store import record


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    operational = (MemoryOperationalStore() if request.param == "memory" else
                   SqliteOperationalStore(SqliteDatabase(tmp_path / "operational.db")))
    yield operational
    if request.param == "sqlite":
        operational.close()


async def arranged(store, monkeypatch, state="pending"):
    old = record()
    old["review"].update(status="awaiting_review", evaluation_node_id="evaluation",
        contract={"message": "Explicit", "author_name": "Author", "author_email": "a@example.com"})
    old["fingerprint"] = fingerprint(old["review"])
    now = datetime.now(UTC)
    old.update(created_at=(now - timedelta(hours=2)).isoformat(),
               expires_at=(now - timedelta(hours=1)).isoformat())
    await store.save_commit_request(old)
    if state != "pending":
        class Earlier(datetime):
            @classmethod
            def now(cls, tz=None):
                return now - timedelta(minutes=90)

        with monkeypatch.context() as patch:
            patch.setattr("Sprout.storage.local.memory.operational.datetime", Earlier)
            patch.setattr("Sprout.storage.local.sqlite.commit_request.datetime", Earlier)
            assert await store.decide_commit_request(old["id"], creator="owner",
                expected_fingerprint=old["fingerprint"], approved=state == "approved",
                reason="Earlier explicit decision")
    metadata = SimpleNamespace(get_execution_node=AsyncMock(return_value=None),
                               get_git_transaction=AsyncMock(return_value=None))
    service = ExperimentCommitService(SimpleNamespace(storage=SimpleNamespace(
        operational=store, metadata=metadata)))
    service.review = AsyncMock(return_value=deepcopy(old["review"]))
    monkeypatch.setattr("Sprout.runtime.experiment_commits.current_cli_principal",
                        lambda: Principal(user_id="owner", source="cli", authenticated=True))
    return old, service, metadata


@pytest.mark.parametrize("state", ["pending", "approved"])
async def test_refresh_preserves_old_and_requires_new_decision(store, monkeypatch, state):
    old, service, _ = await arranged(store, monkeypatch, state)
    before = await store.get_commit_request(old["id"])
    new = await service.refresh(old["id"], expected_fingerprint=old["fingerprint"])
    assert new["id"] != old["id"] and new["supersedes"] == old["id"]
    assert new["state"] == "pending" and new["decision"] is None
    assert new["dispatch_state"] is None
    assert await store.get_commit_request(old["id"]) == before
    assert await service.refresh(old["id"], expected_fingerprint=old["fingerprint"]) == new
    assert await store.get_commit_dispatch(new["id"]) is None
    assert await store.decide_commit_request(new["id"], creator="owner",
        expected_fingerprint=new["fingerprint"], approved=True, reason="New explicit decision")


@pytest.mark.parametrize("blocker", ["fingerprint", "node", "transaction", "dispatch"])
async def test_refresh_refuses_existing_work(store, monkeypatch, blocker):
    old, service, metadata = await arranged(store, monkeypatch)
    if blocker == "node":
        metadata.get_execution_node.return_value = {"state": "uncertain"}
    elif blocker == "transaction":
        metadata.get_git_transaction.return_value = {"state": "active"}
    elif blocker == "dispatch":
        monkeypatch.setattr(store, "get_commit_dispatch",
                            AsyncMock(return_value={"state": "pending"}))
    with pytest.raises(ValueError):
        await service.refresh(old["id"], expected_fingerprint=(
            "wrong" if blocker == "fingerprint" else old["fingerprint"]))
    service.review.assert_not_awaited()


async def test_rejected_request_cannot_refresh(store, monkeypatch):
    old, service, _ = await arranged(store, monkeypatch, "rejected")
    with pytest.raises(ValueError):
        await service.refresh(old["id"], expected_fingerprint=old["fingerprint"])
    service.review.assert_not_awaited()
