"""Offline SDK worker preflight; no server or executable downloads."""

import pytest
from temporalio import workflow
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from Sprout.orchestration.temporal_workflows import (
    SproutExecutionWorkflow,
    SproutGatewayWorkflow,
    SproutGrowthWorkflow,
    SproutTaskWorkflow,
)


@pytest.mark.parametrize(
    "definition",
    [
        SproutExecutionWorkflow,
        SproutGatewayWorkflow,
        SproutGrowthWorkflow,
        SproutTaskWorkflow,
    ],
)
async def test_worker_workflow_can_load_in_default_sdk_sandbox(definition):
    # Use the same validation boundary as Worker, without opening a client.
    SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(definition))
