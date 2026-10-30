"""B4 functional wiring; captured backend is a mock, not OS-open evidence."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from Sprout.agent.executor import ActionExecutor
from Sprout.llm.messages import ToolCall
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b3_node_publication import publication as publication
from Sprout.tests.test_r04_pa2_b1_binding import node_scope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.workspace.models import ReadPlan, ResourceRef


async def test_analysis_reaches_model_tool_result(rig, captured):
    with node_scope(rig):
        tool = await rig.runtime._issue_project_analysis_tool(paths=("src/main.py",))
        executor = rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
        result = await ActionExecutor(executor).run_call(ToolCall(
            id="analysis", name=tool.spec.name,
            arguments={"path": str(rig.root / "src"), "trusted": True}),
            session_id=rig.session.id)
        assert result.ok, result.error
        assert result.data["languages"] == ["python"]
        assert result.data["coverage"]["mode"] == "files"
        assert "main" in result.to_text()
    assert captured.opens == ["src/main.py"]


@pytest.mark.parametrize("operation", ["symbols", "neighbors", "subgraph",
                                       "dependencies", "knowledge"])
async def test_query_five_operations(rig, captured, operation):
    with node_scope(rig):
        tool = await rig.runtime._issue_project_query_tool(paths=("src/main.py",))
        executor = rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
        result = await executor.execute(tool.spec.name, {
            "workspace_id": rig.workspace.id, "operation": operation,
            "name": "main", "path": "src/main.py"}, session_id=rig.session.id)
        assert result.ok, result.error
        assert result.to_text()
        assert "private" not in repr(result.data)
    assert captured.opens == ["src/main.py"]


async def test_model_cannot_expand_fixed_host_selection(rig, captured):
    with node_scope(rig):
        tool = await rig.runtime._issue_project_analysis_tool(paths=("src/main.py",))
        executor = rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
        result = await executor.execute(tool.spec.name, {
            "path": str(rig.root / "src/private"), "paths": ["src/private/denied.py"]})
        assert not result.ok
    assert not captured.opens


async def test_bound_files_do_not_consume_unusable_approval(rig, captured, monkeypatch):
    with node_scope(rig):
        tool = await rig.runtime._issue_project_analysis_tool(paths=("src/main.py",))
        tool.spec = replace(tool.spec, risk_level="high")
        executor = rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
        manager = AsyncMock()
        executor.approvals = manager
        result = await executor.execute(tool.spec.name, {"path": str(rig.root / "src")})
        assert not result.ok
        manager.is_approved.assert_not_called()
        manager.request.assert_not_called()
    assert not captured.opens


async def test_unbound_callback_still_denied(rig):
    tool = rig.runtime.tools.get("project_analyze")
    tool._analyze = AsyncMock(side_effect=AssertionError("legacy fallback"))
    result = await tool.invoke({"path": str(rig.root)})
    assert not result.ok
    tool._analyze.assert_not_called()


async def next_node(rig, kind, dependencies=(), metadata=None):
    node = ExecutionNode(id="b4-" + kind.value, task_id=rig.task.id, type=kind,
                         dependencies=dependencies, metadata=metadata or {})
    await rig.storage.metadata.save_execution_node(node)
    assert await rig.commission(node, "b4-owner", "")
    return await rig.storage.metadata.get_execution_node(node.id)


async def save_result(rig, node, output):
    assert await rig.storage.metadata.finish_execution_node(replace(
        node, status=NodeStatus.COMPLETED, metadata={**node.metadata, "output": output}),
        "b4-owner", "completed")


@pytest.mark.parametrize("change", [None, "scope", "tamper", "runtime"])
async def test_read_plan_subtask_agent_live_products(rig, captured, publication, change):
    executor, messages = publication
    plan = ReadPlan(task_id=rig.task.id, resources=(
        ResourceRef(rig.workspace.id, "src/main.py"),))
    read = await next_node(rig, NodeType.READ)
    output = await executor.execute_node(read, rig.workspace, plan, rig.task)
    if change == "scope":
        rig.task = replace(rig.task, delegation_scope=replace(
            rig.task.delegation_scope, denied_paths=(str(rig.root / "src/main.py"),)))
        await rig.storage.metadata.save_task(rig.task)
    if change == "tamper":
        output[0]["content"] = "TAMPERED"
    await save_result(rig, read, output)
    if change == "runtime":
        from copy import copy

        other = copy(rig.runtime)
        other._node_products = dict(rig.runtime._node_products)
        executor = other._executor(rig.storage.metadata)
    planning = await next_node(rig, NodeType.PLAN, (read.id,))
    if change:
        with pytest.raises(PermissionError):
            await executor.execute_node(planning, rig.workspace, plan, rig.task)
        assert not messages
        return
    planned = await executor.execute_node(planning, rig.workspace, plan, rig.task)
    await save_result(rig, planning, planned)
    sub = await next_node(rig, NodeType.SUBTASK, (planning.id,),
                          {"description": "inspect main", "index": 0, "kind": "analysis"})
    findings = await executor.execute_node(sub, rig.workspace, plan, rig.task)
    await save_result(rig, sub, findings)
    agent = await next_node(rig, NodeType.AGENT, (sub.id,))
    result = await executor.execute_node(agent, rig.workspace, plan, rig.task)
    assert result["content"] == "plain response"
    assert "def main" in repr(messages)
    assert "Plan from the planning step" in repr(messages)
    assert "Findings from the plan" in repr(messages)


async def test_runtime_original_user_history_reaches_model(rig):
    from Sprout.agent.base import AgentResult, ModelAgent
    from Sprout.llm.messages import LLMResponse
    from Sprout.message.models import Message

    seen = []

    class Model:
        async def chat(self, messages, **kwargs):
            seen.extend(messages)
            return LLMResponse(content="current answer")

    first = Message(user_id="owner", session_id=rig.session.id, content="my original question")
    await rig.runtime._persist_turns(rig.session, first, AgentResult(content="old project derived"))
    second = Message(user_id="owner", session_id=rig.session.id, content="follow up")
    context = await rig.runtime._contexts.build(
        message=second, session=rig.session, tools={}, skills={})
    await ModelAgent(Model()).run(second, context)
    assert "my original question" in repr(seen)
    assert "old project derived" not in repr(seen)


async def test_unknown_history_is_not_blessed_by_metadata(rig):
    from Sprout.message.models import Message
    from Sprout.session.models import Turn

    await rig.runtime._session_store.append_turn(Turn(
        rig.session.id, "user", "UNKNOWN_SOURCE", metadata={
            "trusted": True, "user_id": "owner", "message_id": "forged"}))
    context = await rig.runtime._contexts.build(
        message=Message(user_id="owner", content="hi"), session=rig.session, tools={}, skills={})
    assert not context.memory


async def test_existing_code_graph_data_flow(rig, captured, publication, monkeypatch):
    from Sprout.orchestration.compiler import ExecutionGraphBuilder
    from Sprout.orchestration.service import SequentialOrchestrator
    from Sprout.task.models import TaskStatus
    from Sprout.workspace.models import WorkspaceManifest

    # Real graph/compiler/orchestrator/owner settlement; native effects are replaced.
    assert await rig.storage.metadata.finish_execution_node(
        replace(rig.node, status=NodeStatus.COMPLETED), "fixture-owner", "completed")
    # This integration test now performs real first claims. B1's observation
    # fixture prohibits claims only to verify its issuer has no side effects.
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    await rig.storage.metadata.save_task(replace(rig.task, status=TaskStatus.READY))
    executor, messages = publication
    executor._sandbox = AsyncMock(return_value={"status": "not_applicable"})
    executor._evaluation = AsyncMock(return_value={"status": "passed"})
    executor._approval = AsyncMock(return_value={"approved": True})
    executor._apply = AsyncMock(return_value={"status": "controlled-no-effect"})
    plan = ReadPlan(task_id=rig.task.id, resources=(
        ResourceRef(rig.workspace.id, "src/main.py"),))
    graph = ExecutionGraphBuilder().build(
        rig.task, plan, WorkspaceManifest(rig.workspace.id),
        steps=({"description": "investigate main", "kind": "analysis"},))
    result = await SequentialOrchestrator(rig.storage.metadata).execute(
        graph, handler=executor.handler(rig.task, rig.workspace, plan), budget=rig.task.budget)
    assert result.status is TaskStatus.COMPLETED, result
    assert "def main" in repr(messages)
    assert "Plan from the planning step" in repr(messages)
    assert "Findings from the plan" in repr(messages)
    nodes = await rig.storage.metadata.list_execution_nodes(rig.task.id)
    final = next(node for node in nodes if node.id == rig.task.id + ":agent")
    assert final.metadata["output"]["content"] == "plain response"


async def test_actual_agent_loop_receives_analysis_and_query(rig, captured, monkeypatch):
    from unittest.mock import Mock

    from Sprout.agent.loop import AgentLoop
    from Sprout.context.context import AgentContext
    from Sprout.llm.messages import LLMResponse
    from Sprout.message.models import Message

    steps = Mock()
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: steps)

    seen = []

    class Model:
        async def chat(self, messages, **kwargs):
            seen.append(list(messages))
            if len(seen) == 1:
                return LLMResponse(tool_calls=(ToolCall(
                    id="analyze", name="project_analyze",
                    arguments={"path": str(rig.root / "src")}),))
            if len(seen) == 2:
                return LLMResponse(tool_calls=(ToolCall(
                    id="query", name="workspace_query", arguments={
                        "workspace_id": rig.workspace.id, "operation": "symbols",
                        "query": "main"}),))
            return LLMResponse(content="used project main symbol")

    with node_scope(rig):
        tools = [await rig.runtime._issue_project_analysis_tool(paths=("src/main.py",)),
                 await rig.runtime._issue_project_query_tool(paths=("src/main.py",))]
        agent = AgentLoop(Model(), executor=ActionExecutor(rig.executor))
        result = await agent.run(Message(user_id="owner", content="explain project"),
            AgentContext(session=rig.session, tools={tool.spec.name: tool for tool in tools}))
    assert result.content == "used project main symbol"
    tool_messages = [message for message in seen[-1] if str(message.role) == "tool"]
    assert len(tool_messages) == 2
    assert all("main" in message.content for message in tool_messages)


@pytest.mark.parametrize("change", ["file_deny", "expired", "workspace", "owner"])
async def test_bound_tools_recheck_authority(rig, captured, change):
    from datetime import UTC, datetime, timedelta

    with node_scope(rig):
        tool = await rig.runtime._issue_project_query_tool(paths=("src/main.py",))
        if change in {"file_deny", "expired"}:
            scope = (replace(rig.task.delegation_scope, denied_actions=frozenset({"file.read"}))
                     if change == "file_deny" else replace(rig.task.delegation_scope,
                        expires_at=datetime.now(UTC) - timedelta(seconds=1)))
            await rig.storage.metadata.save_task(replace(rig.task, delegation_scope=scope))
        elif change == "workspace":
            await rig.storage.metadata.save_task(replace(rig.task, workspace_id="other"))
        else:
            await rig.storage.metadata.save_task(replace(
                rig.task, actor=replace(rig.principal, user_id="other")))
        result = await rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True).execute(
            tool.spec.name, {"workspace_id": rig.workspace.id, "operation": "symbols"})
        assert not result.ok
    assert not captured.opens


async def test_runtime_owned_read_only_entry_without_host_scan(rig, captured, publication,
                                                             monkeypatch):
    rig.task = replace(rig.task, metadata={**rig.task.metadata,
        "execution_mode": "read_only", "require_acceptance_tests": False,
        "required_paths": ["src/main.py"]})
    await rig.storage.metadata.save_task(rig.task)
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    monkeypatch.setattr(type(rig.runtime._scanner), "scan",
                        AsyncMock(side_effect=AssertionError("host scan reached")))
    read = ExecutionNode(id="owned-read", task_id=rig.task.id, type=NodeType.READ)
    result = await rig.runtime.execute_node(read)
    assert result["status"] == "completed", result
    agent = ExecutionNode(id="owned-agent", task_id=rig.task.id, type=NodeType.AGENT,
                          dependencies=(read.id,), metadata={"read_only": True})
    result = await rig.runtime.execute_node(agent)
    assert result["status"] == "completed", result
    assert "def main" in repr(publication[1])
