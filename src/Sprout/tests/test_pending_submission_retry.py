from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.client import Client

from Sprout.gateway.identity import Principal
from Sprout.gateway.sdk_gateway import SDKGateway
from Sprout.orchestration.terminal.temporal import TemporalConfig, TemporalOrchestratorBackend
from Sprout.tests.test_persisted_task_submission import make_runtime


async def setup_pending(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    owner = Principal(user_id="owner", authenticated=True, source="sdk-host")
    runtime._orchestrator_backend = TemporalOrchestratorBackend(
        TemporalConfig(host="original-host", namespace="original-ns", task_queue="original-queue"))
    monkeypatch.setattr(Client, "connect", AsyncMock(side_effect=ConnectionError("offline")))
    workspace = await runtime.open_workspace(tmp_path)
    task = await runtime.create_task_planned(workspace.id, "explain", actor=owner,
                                           source="sdk", execution_mode="read_only")
    gateway = SDKGateway(runtime, principal=owner)
    await gateway.submit_persisted_task(task.id)
    record = (await runtime.get_task_submissions(task.id))[0]
    return runtime, task, gateway, record


async def test_retry_uses_original_payload_route_and_id(tmp_path, monkeypatch):
    runtime, task, gateway, record = await setup_pending(tmp_path, monkeypatch)
    handle = SimpleNamespace(first_execution_run_id="run", result=AsyncMock())
    client = SimpleNamespace(start_workflow=AsyncMock(return_value=handle))
    connect = AsyncMock(return_value=client)
    monkeypatch.setattr(Client, "connect", connect)
    runtime._orchestrator_backend = None
    try:
        result = await gateway.retry_submission(task.id, submission_id=record["id"])
        assert result["retry_requested"] and not result["new_submission"]
        connect.assert_awaited_once_with("original-host", namespace="original-ns")
        call = client.start_workflow.await_args
        assert call.args[1] == record["payload"]
        assert call.kwargs["id"] == record["id"]
        assert call.kwargs["task_queue"] == "original-queue"
        handle.result.assert_not_awaited()
        assert len(await runtime.get_task_submissions(task.id)) == 1
        with pytest.raises(ValueError):
            await gateway.retry_submission(task.id, submission_id=record["id"])
        client.start_workflow.assert_awaited_once()
    finally:
        await runtime.storage.close()


@pytest.mark.parametrize("change", [
    {"state": "uncertain", "phase": "start"},
    {"state": "pending", "phase": None},
    {"accepted": True, "run_id": "run"},
    {"state": "resolved", "result": {"status": "completed"}},
])
async def test_unknown_or_started_result_is_not_retried(tmp_path, monkeypatch, change):
    runtime, task, gateway, record = await setup_pending(tmp_path, monkeypatch)
    try:
        await runtime.storage.metadata.update_task_submission({**record, **change})
        connect = AsyncMock()
        monkeypatch.setattr(Client, "connect", connect)
        with pytest.raises(ValueError):
            await gateway.retry_submission(task.id, submission_id=record["id"])
        connect.assert_not_awaited()
    finally:
        await runtime.storage.close()


async def test_repeated_connect_failure_keeps_pending_record(tmp_path, monkeypatch):
    runtime, task, gateway, record = await setup_pending(tmp_path, monkeypatch)
    try:
        result = await gateway.retry_submission(task.id, submission_id=record["id"])
        assert result["status"] == "waiting_resource"
        records = await runtime.get_task_submissions(task.id)
        assert len(records) == 1 and records[0]["phase"] == "connect"
        assert records[0]["payload"] == record["payload"]
        assert await runtime.storage.metadata.list_execution_nodes(task.id) == []
    finally:
        await runtime.storage.close()
