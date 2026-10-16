import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.client import Client

from Sprout.orchestration.models import ExecutionGraph
from Sprout.orchestration.terminal.temporal import TemporalOrchestratorBackend
from Sprout.task.models import TaskStatus


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan"), "240"])
def test_invalid_wait_rejected(value):
    with pytest.raises(ValueError):
        TemporalOrchestratorBackend(result_wait_seconds=value)


@pytest.mark.parametrize("seconds", [30.0, 240.0])
async def test_only_result_wait_changes_without_extra_submission(monkeypatch, seconds):
    handle = SimpleNamespace(first_execution_run_id="run",
                             result=AsyncMock(return_value={"status": "completed"}))
    client = SimpleNamespace(start_workflow=AsyncMock(return_value=handle))
    monkeypatch.setattr(Client, "connect", AsyncMock(return_value=client))
    original = asyncio.wait_for
    timeouts = []

    async def observe(awaitable, timeout):
        timeouts.append(timeout)
        return await original(awaitable, timeout=timeout)

    monkeypatch.setattr(asyncio, "wait_for", observe)
    backend = (TemporalOrchestratorBackend() if seconds == 30 else
               TemporalOrchestratorBackend(result_wait_seconds=seconds))
    result = await backend.execute(ExecutionGraph(task_id="fixture", nodes=()))
    assert result.status is TaskStatus.COMPLETED
    assert timeouts == [10, 10, seconds]
    assert client.start_workflow.await_count == 1
    assert handle.result.await_count == 1
