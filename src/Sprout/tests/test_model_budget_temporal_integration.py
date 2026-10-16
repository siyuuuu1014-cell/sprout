"""Real Temporal scheduling/SQLite ownership with a local model, no paid API."""

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

from Sprout.agent.executor import ActionExecutor
from Sprout.agent.loop import AgentLoop
from Sprout.context.context import AgentContext
from Sprout.llm.base import ModelBudgetExhausted
from Sprout.llm.messages import LLMResponse, ToolCall
from Sprout.message.models import Message
from Sprout.orchestration.execution_guard import NodeExecutionGuard
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.orchestration.temporal_codec import node_from_dict, node_to_dict
from Sprout.orchestration.temporal_workflows import SproutExecutionWorkflow
from Sprout.session.models import Session
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task
from Sprout.tests.conftest import EchoTool
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry


async def test_budget_stop_parks_workflow_without_scheduling_followups(tmp_path):
    host = os.getenv("SPROUT_TEMPORAL_TEST_HOST")
    if not host:
        pytest.skip("Dedicated Temporal server required; no model API calls")
    assert host == "127.0.0.1:17233"
    namespace, queue = "sprout-validation-" + uuid4().hex, "budget-" + uuid4().hex
    admin = await Client.connect(host)
    await admin.workflow_service.register_namespace(RegisterNamespaceRequest(
        namespace=namespace, workflow_execution_retention_period=Duration(seconds=86400)),
        timeout=timedelta(seconds=10))
    client = await Client.connect(host, namespace=namespace)
    path = tmp_path / "metadata.db"
    store = open_metadata_store(str(path))
    task = Task(metadata={"execution_mode": "read_only"})
    await store.save_task(task)
    node = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
    calls = []

    class LocalModel:
        name = "no-network-budget-fixture"

        async def chat(self, messages, *, tools=()):
            calls.append("model")
            if len(calls) > 1:
                raise ModelBudgetExhausted("explicit local fixture limit")
            return LLMResponse(tool_calls=(ToolCall("read", "echo_tool", {"text": "read"}),))

    tools = ToolRegistry()
    tools.register(EchoTool())
    loop = AgentLoop(model=LocalModel(),
        executor=ActionExecutor(tools=ToolExecutor(tools=tools)))

    async def execute(running):
        result = await loop.run(Message("inspect"), AgentContext(
            session=Session(id="fixture", user_id="fixture"), user={"id": "fixture"},
            tools={"echo_tool": tools.get("echo_tool")}))
        pytest.fail(f"Budget stop must not become node success: {result.metadata}")

    @activity.defn(name="run_sprout_node")
    async def guarded_activity(payload):
        assert payload["id"] == node.id, "Follow-up node must not be scheduled"
        return await NodeExecutionGuard(store).execute(node_from_dict(payload), execute)

    graph = {"task_id": task.id, "nodes": [node_to_dict(node),
        node_to_dict(ExecutionNode(task_id=task.id, type=NodeType.EVALUATION)),
        node_to_dict(ExecutionNode(task_id=task.id, type=NodeType.APPLY))]}
    try:
        async with Worker(client, task_queue=queue, workflows=[SproutExecutionWorkflow],
                          activities=[guarded_activity]):
            handle = await client.start_workflow(SproutExecutionWorkflow.run, graph,
                id="budget-" + uuid4().hex, task_queue=queue)
            result = await asyncio.wait_for(handle.result(), timeout=30)
        assert result["status"] == "waiting_resource"
        saved = await store.get_execution_node(node.id)
        assert saved.status == NodeStatus.WAITING
        assert saved.metadata["execution_guard"]["state"] == "uncertain"
        assert "ModelBudgetExhausted" in saved.metadata["error"]
        history = await handle.fetch_history(rpc_timeout=timedelta(seconds=10))
        assert sum(event.HasField("activity_task_scheduled_event_attributes")
                   for event in history.events) == 1
        (tmp_path / "history.json").write_text(history.to_json(), encoding="utf-8")
        replay = await Replayer(workflows=[SproutExecutionWorkflow]).replay_workflow(history)
        assert replay.replay_failure is None
        store.close()
        store = open_metadata_store(str(path))
        repeated = await NodeExecutionGuard(store).execute(node, execute, resume_key="new")
        assert repeated["output"]["waiting_kind"] == "resource"
        assert calls == ["model", "model"]
        assert (await store.get_execution_node(node.id)).attempts == 1
        assert not await store.list_change_proposals(task.id)
    finally:
        store.close()
