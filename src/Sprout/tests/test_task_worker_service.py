from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.client import Client

from Sprout.config.loader import default_settings
from Sprout.orchestration.terminal.temporal import TemporalConfig
from Sprout.orchestration.worker import run_task_worker


async def test_task_worker_does_not_start_generic_runtime_and_closes_on_failure(monkeypatch):
    runtime = SimpleNamespace(start=AsyncMock(), storage=SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr("Sprout.runtime.factory.create_runtime", lambda settings: runtime)
    connect = AsyncMock(side_effect=ConnectionError("offline"))
    monkeypatch.setattr(Client, "connect", connect)
    with pytest.raises(ConnectionError):
        await run_task_worker(TemporalConfig(), settings=default_settings())
    runtime.start.assert_not_called()
    runtime.storage.close.assert_awaited_once()


async def test_only_task_workflow_activity_and_storage_lifecycle(monkeypatch):
    runtime = SimpleNamespace(start=AsyncMock(), storage=SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr("Sprout.runtime.factory.create_runtime", lambda settings: runtime)
    monkeypatch.setattr(Client, "connect", AsyncMock(return_value=object()))
    exited = []

    class Worker:
        def __init__(self, client, **kwargs):
            assert len(kwargs["workflows"]) == 1
            assert len(kwargs["activities"]) == 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            exited.append(True)

    monkeypatch.setattr("temporalio.worker.Worker", Worker)
    ready = Mock(side_effect=RuntimeError("stop fixture"))
    with pytest.raises(RuntimeError, match="stop fixture"):
        await run_task_worker(TemporalConfig(), settings=default_settings(), on_ready=ready)
    assert exited == [True]
    runtime.start.assert_not_called()
    runtime.storage.close.assert_awaited_once()


@pytest.mark.parametrize("dsn", ["memory://", "sqlite:///:memory:", ""])
async def test_task_worker_rejects_volatile_metadata(monkeypatch, dsn):
    factory = Mock()
    monkeypatch.setattr("Sprout.runtime.factory.create_runtime", factory)
    settings = default_settings()
    settings.storage.metadata = dsn
    with pytest.raises(ValueError, match="Persistent metadata"):
        await run_task_worker(TemporalConfig(), settings=settings)
    factory.assert_not_called()
