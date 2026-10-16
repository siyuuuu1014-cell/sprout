import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.orchestration.commit_dispatch import _submit_commit_dispatch
from Sprout.storage.contracts.commit_request import commit_workflow_id
from Sprout.storage.local.memory.operational import MemoryOperationalStore
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.operational import SqliteOperationalStore
from Sprout.tests.test_commit_request_store import record


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


async def arranged(store):
    request = record()
    await store.save_commit_request(request)
    await store.decide_commit_request(request["id"], creator="owner",
        expected_fingerprint=request["fingerprint"], approved=True, reason="Explicit")
    return {"request_id": request["id"], "request_fingerprint": request["fingerprint"],
            "workflow_id": commit_workflow_id(request["id"], request["fingerprint"]),
            "host": "127.0.0.1:17233",
            "namespace": "fixture-only", "task_queue": "fixture-dedicated-queue"}


async def test_delivery_records_before_connect_and_caches_acknowledgement(store, monkeypatch):
    dispatch = await arranged(store)
    start = AsyncMock(return_value=SimpleNamespace(first_execution_run_id="fixture-run"))
    async def connect(*args, **kwargs):
        assert (await store.get_commit_dispatch(dispatch["request_id"]))["state"] == "pending"
        return SimpleNamespace(start_workflow=start)
    monkeypatch.setattr("temporalio.client.Client.connect", connect)
    result = await _submit_commit_dispatch(store, dispatch)
    assert result["dispatch_state"] == "accepted" and result["execution_confirmed"] is False
    assert (await store.get_commit_request(dispatch["request_id"]))["dispatch_state"] == "accepted"
    await _submit_commit_dispatch(store, dispatch)
    assert start.await_count == 1


async def test_timeout_retry_keeps_same_workflow_and_binding(store, monkeypatch):
    dispatch = await arranged(store)
    start = AsyncMock(side_effect=[TimeoutError(), SimpleNamespace(first_execution_run_id="run")])
    monkeypatch.setattr("temporalio.client.Client.connect",
                        AsyncMock(return_value=SimpleNamespace(start_workflow=start)))
    result = await _submit_commit_dispatch(store, dispatch)
    assert result["dispatch_state"] == "uncertain" and result["local_fallback"] is False
    await _submit_commit_dispatch(store, dispatch)
    assert {call.kwargs["id"] for call in start.await_args_list} == {dispatch["workflow_id"]}
    with pytest.raises(ValueError, match="binding"):
        await _submit_commit_dispatch(store, {**dispatch, "task_queue": "different"})
    assert start.await_count == 2


async def test_unapproved_request_cannot_create_dispatch(store):
    request = record()
    await store.save_commit_request(request)
    dispatch = {"request_id": request["id"], "request_fingerprint": request["fingerprint"],
                "workflow_id": commit_workflow_id(request["id"], request["fingerprint"]),
                "host": "host", "namespace": "ns", "task_queue": "q"}
    with pytest.raises(ValueError, match="Approved"):
        await store.ensure_commit_dispatch(dispatch)


async def test_acknowledgement_cannot_regress_to_unknown(store):
    dispatch = await arranged(store)
    await store.ensure_commit_dispatch(dispatch)
    assert await store.mark_commit_dispatch(dispatch["request_id"], "accepted", "run")
    assert not await store.mark_commit_dispatch(dispatch["request_id"], "uncertain")
    assert (await store.get_commit_request(dispatch["request_id"]))["dispatch_state"] == "accepted"


async def test_cancellation_after_start_retains_unconfirmed_dispatch(store, monkeypatch):
    dispatch = await arranged(store)
    start = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr("temporalio.client.Client.connect",
                        AsyncMock(return_value=SimpleNamespace(start_workflow=start)))
    with pytest.raises(asyncio.CancelledError):
        await _submit_commit_dispatch(store, dispatch)
    assert (await store.get_commit_dispatch(dispatch["request_id"]))["state"] == "uncertain"


async def test_sqlite_acknowledgement_and_outbox_update_are_atomic(tmp_path):
    import sqlite3

    store = SqliteOperationalStore(SqliteDatabase(tmp_path / "atomic.db"))
    try:
        dispatch = await arranged(store)
        await store.ensure_commit_dispatch(dispatch)
        store._db.executescript("CREATE TRIGGER fail_outbox BEFORE UPDATE "
            "ON experiment_commit_requests WHEN NEW.dispatch_state='accepted' BEGIN "
            "SELECT RAISE(ABORT, 'test outbox fault'); END;")
        with pytest.raises(sqlite3.IntegrityError, match="outbox fault"):
            await store.mark_commit_dispatch(dispatch["request_id"], "accepted", "run")
        assert (await store.get_commit_dispatch(dispatch["request_id"]))["state"] == "pending"
        assert (await store.get_commit_request(dispatch["request_id"]))[
            "dispatch_state"] == "waiting_schedule"
    finally:
        store.close()


async def test_duplicate_workflow_with_wrong_server_binding_stays_unknown(store, monkeypatch):
    from temporalio.exceptions import WorkflowAlreadyStartedError

    dispatch = await arranged(store)
    description = SimpleNamespace(id=dispatch["workflow_id"], namespace=dispatch["namespace"],
        task_queue="foreign-queue", raw_info=SimpleNamespace(
            type=SimpleNamespace(name="SproutExperimentCommitWorkflow")),
        memo=AsyncMock(return_value={}), run_id="foreign-run")
    handle = SimpleNamespace(describe=AsyncMock(return_value=description))
    client = SimpleNamespace(start_workflow=AsyncMock(side_effect=WorkflowAlreadyStartedError(
        dispatch["workflow_id"], "SproutExperimentCommitWorkflow", run_id="foreign-run")),
        get_workflow_handle=lambda *args, **kwargs: handle)
    monkeypatch.setattr("temporalio.client.Client.connect", AsyncMock(return_value=client))
    result = await _submit_commit_dispatch(store, dispatch)
    assert result["dispatch_state"] == "uncertain"
    assert result["execution_confirmed"] is False
