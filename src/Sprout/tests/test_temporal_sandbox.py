"""Offline SDK worker preflight; no server or executable downloads."""

import pytest
from temporalio import activity, workflow
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from Sprout.orchestration.activities.node import bind_node_activity
from Sprout.orchestration.commit_workflows import SproutExperimentCommitWorkflow
from Sprout.orchestration.isolated_worker import IsolatedCodingWorker, IsolatedCommitWorker
from Sprout.orchestration.temporal_workflows import (
    SproutExecutionWorkflow,
    SproutGatewayWorkflow,
    SproutGrowthWorkflow,
    SproutTaskWorkflow,
    run_growth_automation,
    run_sprout_node,
)


@pytest.mark.parametrize(
    "definition",
    [
        SproutExecutionWorkflow,
        SproutGatewayWorkflow,
        SproutGrowthWorkflow,
        SproutTaskWorkflow,
        SproutExperimentCommitWorkflow,
    ],
)
async def test_worker_workflow_can_load_in_default_sdk_sandbox(definition):
    # Use the same validation boundary as Worker, without opening a client.
    SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(definition))


@pytest.mark.parametrize(("definition", "name"), [
    (run_sprout_node, "run_sprout_node"),
    (bind_node_activity(None), "run_sprout_node"),
    (IsolatedCodingWorker.execute_node, "run_sprout_node"),
    (run_growth_automation, "run_growth_automation"),
    (IsolatedCodingWorker.execute_commit, "run_experiment_commit"),
    (IsolatedCommitWorker.execute_commit, "run_experiment_commit"),
])
def test_temporal_activity_registration_name(definition, name):
    assert activity._Definition.must_from_callable(definition).name == name


@pytest.mark.asyncio
@pytest.mark.parametrize(("outcome", "expected"), [
    ({"output": {"waiting": True}}, "waiting_approval"),
    ({"output": {"waiting": True, "waiting_kind": "resource"}}, "waiting_resource"),
    ({"status": "cancelled"}, "cancelled"),
    (RuntimeError("activity failed"), "failed"),
])
async def test_execution_stops_on_activity_outcome(monkeypatch, outcome, expected):
    calls = []

    async def execute_activity(definition, node, **kwargs):
        calls.append((definition, node["id"]))
        if isinstance(outcome, Exception):
            raise outcome
        return {"node_id": node["id"], **outcome}

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "patched", lambda _: True)
    result = await SproutExecutionWorkflow().run({"nodes": [
        {"id": "completed", "status": "completed"}, {"id": "active"}, {"id": "later"},
    ]})
    assert calls == [(run_sprout_node, "active")]
    assert result["status"] == expected
    assert result["nodes"][0] == {"node_id": "completed", "status": "completed", "skipped": True}
    assert len(result["nodes"]) == 2


@pytest.mark.parametrize("failed", [False, True])
async def test_commit_activity_keeps_single_attempt_contract(monkeypatch, failed):
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from temporalio.common import RetryPolicy

    request = {"request_id": "request", "request_fingerprint": "fingerprint"}
    result = {"applied": True, "execution_confirmed": True}
    execute = AsyncMock(
        return_value=result, side_effect=RuntimeError("unknown") if failed else None,
    )
    monkeypatch.setattr(workflow, "execute_activity", execute)
    actual = await SproutExperimentCommitWorkflow().run(request)
    execute.assert_awaited_once_with(
        "run_experiment_commit", request, start_to_close_timeout=timedelta(seconds=120),
        retry_policy=RetryPolicy(maximum_attempts=1),
    )
    assert actual == (
        {"applied": False, "execution_confirmed": False,
         "reason": "commit_result_unconfirmed", "automatic_retry": False}
        if failed else result
    )
