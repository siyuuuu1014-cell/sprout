"""Shared query contract without models, workers or scheduler connections."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.gateway.identity import Principal
from Sprout.gateway.runtime_gateway import RuntimeGateway
from Sprout.gateway.sdk_gateway import SDKGateway
from Sprout.task.models import Task

OWNER = Principal(user_id="alice", authenticated=True, source="cli")


def runtime_fixture():
    task = Task(actor=OWNER, instruction="demo")
    record = {"id": "submission", "task_id": task.id, "run_id": "run",
              "host": "unused", "namespace": "default", "task_queue": "queue",
              "accepted": True, "state": "resolved", "result": {"status": "completed"}}
    runtime = SimpleNamespace(
        get_task=AsyncMock(return_value=task),
        get_task_execution_report=AsyncMock(return_value={"nodes": []}),
        get_verification_report=AsyncMock(return_value={"verified": False}),
        get_task_budget_report=AsyncMock(return_value={"mode": "legacy_unavailable"}),
        get_task_submissions=AsyncMock(return_value=[record]),
        execute=AsyncMock(),
    )
    return runtime, task


@pytest.mark.asyncio
async def test_sdk_and_other_gateway_share_report_and_observation():
    runtime, task = runtime_fixture()
    cli = RuntimeGateway(runtime, transport="cli")
    sdk = SDKGateway(runtime, principal=OWNER)
    assert await sdk.task_report(task.id) == await cli.queries.report(task.id, principal=OWNER)
    report = await sdk.task_report(task.id)
    assert report["stage"] == "result_available"
    assert report["budget"]["mode"] == "legacy_unavailable"
    assert report["execution_authority"] is False
    observed = await sdk.observe_task(task.id)
    assert observed["observation"] == "persisted_result"
    assert observed["persisted_task_status"] == "created"
    assert observed["new_submission"] is False
    runtime.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("principal", [
    Principal(user_id="alice"),
    Principal(user_id="bob", authenticated=True, source="cli"),
    Principal(user_id="alice", authenticated=True, source="web"),
])
async def test_identity_mismatch_cannot_read_evidence(principal):
    runtime, task = runtime_fixture()
    with pytest.raises(PermissionError):
        await SDKGateway(runtime, principal=principal).task_report(task.id)
    runtime.get_task_execution_report.assert_not_called()
    runtime.get_task_submissions.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_sdk_caller_id_does_not_authenticate_queries():
    runtime, task = runtime_fixture()
    with pytest.raises(PermissionError):
        await SDKGateway(runtime).task_report(task.id)
    runtime.get_task.assert_not_called()


@pytest.mark.asyncio
async def test_ambiguous_history_and_foreign_submission_are_rejected():
    runtime, task = runtime_fixture()
    record = runtime.get_task_submissions.return_value[0]
    runtime.get_task_submissions.return_value = [record, {**record, "id": "other"}]
    sdk = SDKGateway(runtime, principal=OWNER)
    with pytest.raises(ValueError):
        await sdk.observe_task(task.id)
    assert (await sdk.observe_task(task.id, submission_id="other"))["submission_id"] == "other"
    runtime.get_task_submissions.return_value = [{**record, "task_id": "foreign"}]
    with pytest.raises(ValueError):
        await sdk.observe_task(task.id)


@pytest.mark.asyncio
async def test_missing_and_legacy_owner_tasks():
    runtime, task = runtime_fixture()
    sdk = SDKGateway(runtime, principal=OWNER)
    runtime.get_task.return_value = None
    with pytest.raises(LookupError):
        await sdk.task_report(task.id)
    runtime.get_task.return_value = Task(actor=Principal(user_id="alice"))
    with pytest.raises(PermissionError):
        await sdk.task_report(task.id)
