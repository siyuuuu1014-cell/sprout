from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.client import Client

from Sprout.orchestration.submission_observation import observe_submission


def record(**changes):
    return {"id": "workflow", "accepted": True, "run_id": "exact-run", "host": "host",
            "namespace": "namespace", "task_queue": "queue", "state": "uncertain", **changes}


async def test_running_observation_never_waits_submits_or_cancels(monkeypatch):
    handle = SimpleNamespace(describe=AsyncMock(return_value=SimpleNamespace(
        status=SimpleNamespace(name="RUNNING"))), result=AsyncMock(), cancel=AsyncMock())
    client = SimpleNamespace(get_workflow_handle=Mock(return_value=handle),
                             start_workflow=AsyncMock())
    monkeypatch.setattr(Client, "connect", AsyncMock(return_value=client))
    result = await observe_submission(record())
    assert result["observation"] == "running"
    client.get_workflow_handle.assert_called_once_with("workflow", run_id="exact-run")
    client.start_workflow.assert_not_awaited()
    handle.result.assert_not_awaited()
    handle.cancel.assert_not_awaited()


async def test_observation_timeout_is_not_task_cancellation(monkeypatch):
    handle = SimpleNamespace(describe=AsyncMock(return_value=SimpleNamespace(
        status=SimpleNamespace(name="RUNNING"))), result=AsyncMock(side_effect=TimeoutError),
        cancel=AsyncMock())
    client = SimpleNamespace(get_workflow_handle=Mock(return_value=handle))
    monkeypatch.setattr(Client, "connect", AsyncMock(return_value=client))
    result = await observe_submission(record(), wait_seconds=1)
    assert result["observation"] == "wait_expired" and result["task_cancelled"] is False
    handle.cancel.assert_not_awaited()


async def test_resolved_result_does_not_connect(monkeypatch):
    connect = AsyncMock()
    monkeypatch.setattr(Client, "connect", connect)
    result = await observe_submission(record(state="resolved", result={"status": "completed"}))
    assert result["result"]["status"] == "completed"
    connect.assert_not_awaited()


@pytest.mark.parametrize("seconds", [-1, 61, float("nan"), True])
async def test_invalid_wait_rejected(seconds):
    with pytest.raises(ValueError):
        await observe_submission(record(), wait_seconds=seconds)
