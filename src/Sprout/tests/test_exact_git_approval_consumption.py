import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from Sprout.execution.isolated_git_authorization import consume_commit_approval
from Sprout.security.approval import ApprovalStatus
from Sprout.storage.local.memory.operational import MemoryOperationalStore
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.operational import SqliteOperationalStore
from Sprout.tests.test_isolated_git_authorization import write_fixture


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


async def test_exact_grant_consumed_once_under_concurrency(store):
    plan, contract, arguments, record, creator, principal = write_fixture()
    await store.save_approval(record)

    async def consume():
        return await consume_commit_approval(store, approval_id=record.id, arguments=arguments,
            git_plan=plan, write_contract=contract, creator=creator, principal=principal)

    results = await asyncio.gather(consume(), consume())
    assert sum(result["consumed"] for result in results) == 1
    persisted = await store.get_approval(record.id)
    assert persisted.status is ApprovalStatus.CONSUMED
    assert persisted.used_at is not None


async def test_snapshot_drift_blocks_atomic_consumption(store):
    _, _, _, record, _, _ = write_fixture()
    await store.save_approval(record)
    snapshot = await store.get_approval(record.id)
    await store.save_approval(replace(record, resource_scope="different-proposal"))
    assert not await store.consume_approval_exact(snapshot, datetime.now(UTC))
    assert (await store.get_approval(record.id)).status is ApprovalStatus.APPROVED


async def test_options_drift_preserves_unconsumed_grant(store):
    plan, contract, arguments, record, creator, principal = write_fixture()
    await store.save_approval(record)
    contract["message"] = "changed"
    result = await consume_commit_approval(store, approval_id=record.id, arguments=arguments,
        git_plan=plan, write_contract=contract, creator=creator, principal=principal)
    assert result["consumed"] is False
    assert (await store.get_approval(record.id)).used_at is None


async def test_missing_explicit_id_never_uses_another_matching_grant(store):
    plan, contract, arguments, record, creator, principal = write_fixture()
    await store.save_approval(record)
    result = await consume_commit_approval(store, approval_id="missing", arguments=arguments,
        git_plan=plan, write_contract=contract, creator=creator, principal=principal)
    assert result["consumed"] is False
    assert (await store.get_approval(record.id)).status is ApprovalStatus.APPROVED


async def test_sqlite_separate_connections_cannot_consume_same_snapshot(tmp_path):
    # Separate drivers deliberately emulate independent worker processes.
    first = SqliteOperationalStore(SqliteDatabase(tmp_path / "shared.db"))
    second = SqliteOperationalStore(SqliteDatabase(tmp_path / "shared.db"))
    try:
        _, _, _, record, _, _ = write_fixture()
        await first.save_approval(record)
        snapshots = [await store.get_approval(record.id) for store in (first, second)]
        results = await asyncio.gather(*(
            store.consume_approval_exact(snapshot, datetime.now(UTC))
            for store, snapshot in zip((first, second), snapshots, strict=True)))
        assert sum(results) == 1
        assert (await second.get_approval(record.id)).status is ApprovalStatus.CONSUMED
    finally:
        first.close()
        second.close()
