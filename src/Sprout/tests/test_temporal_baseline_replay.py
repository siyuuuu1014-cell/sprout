"""Real server histories from the repository baseline, not production history."""

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio import activity
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.worker import Replayer, Worker

from Sprout.orchestration.temporal_workflows import SproutExecutionWorkflow
from Sprout.tests.temporal_baseline_7b377ac import SproutExecutionWorkflow as BaselineWorkflow


@activity.defn(name="run_sprout_node")
async def baseline_fixture_node(node: dict) -> dict:
    """Fixed activity outputs isolate workflow command compatibility from tools."""
    return {"node_id": node["id"], "status": node.get("fixture_status", "completed"), "output": {}}


@pytest.mark.parametrize(
    "scenario", ["normal", "completed-evaluation", "skipped-node", "cancelled-result"]
)
async def test_repository_baseline_history_replays(tmp_path, scenario):
    host = os.getenv("SPROUT_TEMPORAL_TEST_HOST")
    if not host:
        pytest.skip("Isolated real Temporal server required")
    assert host == "127.0.0.1:17233"
    namespace = "sprout-baseline-" + uuid4().hex
    client = await asyncio.wait_for(Client.connect(host), timeout=15)
    await client.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=namespace, workflow_execution_retention_period=Duration(seconds=86400)
        ),
        timeout=timedelta(seconds=10),
    )
    client = await Client.connect(host, namespace=namespace)
    queue = "baseline-" + uuid4().hex
    nodes = [{"id": "read", "type": "read", "status": "pending", "budget": {}}]
    if scenario == "completed-evaluation":
        nodes = [
            {
                "id": "evaluation",
                "type": "evaluation",
                "status": "completed",
                "metadata": {"output": {"status": "passed"}},
                "budget": {},
            },
            {"id": "agent", "type": "agent", "status": "pending", "budget": {}},
        ]
    elif scenario == "skipped-node":
        nodes[0]["status"] = "skipped"
    elif scenario == "cancelled-result":
        nodes[0]["fixture_status"] = "cancelled"
        nodes.append({"id": "next", "type": "read", "status": "pending", "budget": {}})
    async with Worker(
        client, task_queue=queue, workflows=[BaselineWorkflow], activities=[baseline_fixture_node]
    ):
        handle = await client.start_workflow(
            "SproutExecutionWorkflow",
            {"nodes": nodes},
            id="baseline-" + uuid4().hex,
            task_queue=queue,
        )
        assert (await asyncio.wait_for(handle.result(), timeout=20))["status"] == "completed"
        history = await handle.fetch_history(rpc_timeout=timedelta(seconds=10))
    (tmp_path / "baseline-history.json").write_text(history.to_json(), encoding="utf-8")
    await asyncio.wait_for(
        Replayer(workflows=[SproutExecutionWorkflow]).replay_workflow(history), timeout=20
    )
    async with Worker(
        client,
        task_queue=queue,
        workflows=[SproutExecutionWorkflow],
        activities=[baseline_fixture_node],
    ):
        handle = await client.start_workflow(
            "SproutExecutionWorkflow",
            {"nodes": nodes},
            id="current-" + uuid4().hex,
            task_queue=queue,
        )
        result = await asyncio.wait_for(handle.result(), timeout=20)
        assert result["status"] == ("cancelled" if scenario == "cancelled-result" else "completed")
        current_history = await handle.fetch_history(rpc_timeout=timedelta(seconds=10))
    scheduled = sum(
        event.HasField("activity_task_scheduled_event_attributes")
        for event in current_history.events
    )
    assert scheduled == (0 if scenario in {"completed-evaluation", "skipped-node"} else 1)
    (tmp_path / "current-history.json").write_text(current_history.to_json(), encoding="utf-8")
    await Replayer(workflows=[SproutExecutionWorkflow]).replay_workflow(current_history)
