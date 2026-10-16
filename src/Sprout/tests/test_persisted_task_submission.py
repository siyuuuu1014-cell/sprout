from unittest.mock import AsyncMock

import pytest
from temporalio.client import Client

from Sprout.config.loader import default_settings
from Sprout.gateway.identity import Principal
from Sprout.gateway.sdk_gateway import SDKGateway
from Sprout.orchestration.terminal.temporal import TemporalConfig, TemporalOrchestratorBackend
from Sprout.runtime.factory import create_runtime


def make_runtime(tmp_path):
    settings = default_settings()
    settings.model.provider = "echo"
    settings.model.model = "echo-1"
    settings.storage.operational = "memory://"
    settings.storage.knowledge = "memory://"
    settings.storage.observations.enabled = False
    settings.storage.metadata = f"sqlite:///{tmp_path / 'metadata.db'}"
    settings.storage.trajectory_dir = str(tmp_path / "trajectory")
    settings.storage.blobs_dir = str(tmp_path / "blobs")
    settings.security.audit.path = str(tmp_path / "audit.jsonl")
    return create_runtime(settings)


async def test_sdk_submission_is_durable_without_worker_or_result_wait(tmp_path, monkeypatch):
    from types import SimpleNamespace

    runtime = make_runtime(tmp_path)
    principal = Principal(user_id="owner", authenticated=True, source="sdk-host")
    backend = TemporalOrchestratorBackend(TemporalConfig(host="configured", namespace="ns"))
    runtime._orchestrator_backend = backend
    handle = SimpleNamespace(first_execution_run_id="run", result=AsyncMock())
    client = SimpleNamespace(start_workflow=AsyncMock(return_value=handle))
    connect = AsyncMock(return_value=client)
    monkeypatch.setattr(Client, "connect", connect)
    try:
        workspace = await runtime.open_workspace(tmp_path)
        task = await runtime.create_task_planned(
            workspace.id, "explain", actor=principal, source="sdk", execution_mode="read_only")
        gateway = SDKGateway(runtime, principal=principal)
        result = await gateway.submit_persisted_task(task.id)
        assert result["new_submission"] is True
        assert result["worker_started"] is False
        assert result["result_waited"] is False
        records = await runtime.get_task_submissions(task.id)
        assert len(records) == 1 and records[0]["run_id"] == "run"
        assert records[0]["host"] == "configured"
        assert records[0]["state"] == "accepted"
        again = await gateway.submit_persisted_task(task.id)
        assert again["new_submission"] is False
        client.start_workflow.assert_awaited_once()
        handle.result.assert_not_awaited()
        assert runtime._orchestrator_backend is backend
    finally:
        await runtime.stop()


async def test_unavailable_scheduler_keeps_record_and_does_not_auto_retry(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    principal = Principal(user_id="owner", authenticated=True, source="sdk-host")
    runtime._orchestrator_backend = TemporalOrchestratorBackend(TemporalConfig(host="configured"))
    connect = AsyncMock(side_effect=ConnectionError("offline"))
    monkeypatch.setattr(Client, "connect", connect)
    try:
        workspace = await runtime.open_workspace(tmp_path)
        task = await runtime.create_task_planned(
            workspace.id, "explain", actor=principal, source="sdk", execution_mode="read_only")
        gateway = SDKGateway(runtime, principal=principal)
        await gateway.submit_persisted_task(task.id)
        records = await runtime.get_task_submissions(task.id)
        assert len(records) == 1 and not records[0].get("accepted")
        assert (await gateway.submit_persisted_task(task.id))["new_submission"] is False
        connect.assert_awaited_once()
        assert await runtime.storage.metadata.list_execution_nodes(task.id) == []
    finally:
        await runtime.stop()


@pytest.mark.parametrize("reason", ["backend", "replay", "nodes"])
async def test_submit_rejects_incompatible_admission(tmp_path, reason):
    from dataclasses import replace

    from Sprout.orchestration.models import ExecutionNode

    runtime = make_runtime(tmp_path)
    principal = Principal(user_id="owner", authenticated=True, source="sdk-host")
    try:
        workspace = await runtime.open_workspace(tmp_path)
        task = await runtime.create_task_planned(
            workspace.id, "explain", actor=principal, source="sdk", execution_mode="read_only")
        if reason == "replay":
            await runtime.storage.metadata.save_task(
                replace(task, metadata={"replay_plan": {"v": 1}}))
        elif reason == "nodes":
            await runtime.storage.metadata.save_execution_node(ExecutionNode(task_id=task.id))
        else:
            runtime._orchestrator_backend = None
        with pytest.raises(ValueError):
            await SDKGateway(runtime, principal=principal).submit_persisted_task(task.id)
        assert await runtime.get_task_submissions(task.id) == []
    finally:
        await runtime.stop()
