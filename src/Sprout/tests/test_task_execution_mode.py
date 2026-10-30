"""Explicit intent cannot be inferred or weakened by instruction wording."""

from dataclasses import replace

import pytest

from Sprout.gateway.task_adapter import GatewayRequest, TaskAdapter
from Sprout.llm.messages import LLMResponse, ToolCall
from Sprout.orchestration.models import ExecutionNode, NodeType
from Sprout.task.models import Task, TaskExecutionMode, TaskStatus, execution_metadata
from Sprout.tests.readonly_host_fixtures import authorized_read_host as authorized_read_host
from Sprout.tests.test_workspace_consent import _runtime, _runtime_with_agent
from Sprout.tools.result import ToolResult
from Sprout.tools.spec import ToolSpec


def test_task_adapter_defaults_to_code_and_overrides_weak_metadata():
    task = TaskAdapter().to_task(GatewayRequest(
        transport="test", instruction="explain",
        metadata={"execution_mode": "read_only", "require_acceptance_tests": False},
    ))
    assert task.execution_mode == TaskExecutionMode.CODE
    assert task.metadata["require_acceptance_tests"] is True
    assert Task().execution_mode == TaskExecutionMode.UNCONFIRMED
    assert Task(metadata={"execution_mode": "invalid"}).execution_mode == "unconfirmed"
    with pytest.raises(ValueError):
        execution_metadata({}, "unconfirmed")


async def test_non_git_requires_explicit_read_only_and_legacy_confirmation(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        workspace = await runtime.open_workspace(tmp_path)
        with pytest.raises(ValueError, match="Git"):
            await runtime.create_task_planned(workspace.id, "explain")
        readonly = await runtime.create_task_planned(
            workspace.id, "write code", execution_mode="read_only",
        )
        assert readonly.execution_mode == "read_only"
        assert readonly.metadata["require_acceptance_tests"] is False
        legacy = Task(workspace_id=workspace.id, instruction="explain")
        await runtime.storage.metadata.save_task(legacy)
        with pytest.raises(ValueError, match="unconfirmed"):
            await runtime._execute_task(legacy, artifact_snapshot=None)
        with pytest.raises(ValueError, match="unconfirmed"):
            await runtime.resume_task(legacy)
        with pytest.raises(ValueError, match="unconfirmed"):
            await runtime.execute_node(ExecutionNode(task_id=legacy.id, type=NodeType.SANDBOX))
        confirmed = await runtime.confirm_task_mode(legacy.id, "read_only")
        assert confirmed.execution_mode == "read_only"
        with pytest.raises(ValueError, match="Read-only"):
            await runtime.execute_node(ExecutionNode(task_id=legacy.id, type=NodeType.SANDBOX))
        with pytest.raises(ValueError, match="already confirmed"):
            await runtime.confirm_task_mode(legacy.id, "code")
        started = replace(legacy, id="started", status=TaskStatus.WAITING_APPROVAL)
        await runtime.storage.metadata.save_task(started)
        with pytest.raises(ValueError, match="execution evidence"):
            await runtime.confirm_task_mode(started.id, "read_only")
    finally:
        await runtime.stop()


@pytest.mark.parametrize("git", [False, True])
async def test_read_only_analysis_never_offers_write_tools(tmp_path, git, authorized_read_host):
    class RecordingModel:
        name = "recording"

        def __init__(self):
            self.tools = []

        async def chat(self, messages, *, tools=()):
            self.tools.append(tuple(spec.name for spec in tools))
            return LLMResponse(content="Analysis only", finish_reason="stop", model=self.name)

    root = tmp_path / "source"
    root.mkdir()
    source = root / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    if git:
        (root / ".git").mkdir()
    runtime, model = _runtime_with_agent(tmp_path, RecordingModel())
    try:
        workspace = await runtime.open_workspace(root)
        principal, scope = authorized_read_host(source, source="test-host")
        task = await runtime.create_task_planned(
            workspace.id, "overwrite app.py", execution_mode="read_only",
            actor=principal, source=principal.source, delegation_scope=scope,
        )
        result = await runtime.execute(task)
        assert result.status == TaskStatus.COMPLETED
        # B4 advertises bound read-only analysis/query tools, never write tools.
        assert model.tools and all(
            set(tools) <= {"project_analyze", "workspace_query"} for tools in model.tools
        )
        assert source.read_text(encoding="utf-8") == "value = 1\n"
        nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
        assert {node.type for node in nodes} == {NodeType.READ, NodeType.AGENT}
        assert not await runtime.storage.metadata.list_change_proposals(task.id)
    finally:
        await runtime.stop()


async def test_mcp_task_creation_preserves_explicit_mode_and_source(tmp_path):
    from Sprout.mcp.server.tools import register_tools

    class RecordingServer:
        def __init__(self):
            self.handlers = {}

        def tool(self, *, name, description):
            def register(handler):
                self.handlers[name] = handler
                return handler
            return register

    runtime = _runtime(tmp_path)
    try:
        workspace = await runtime.open_workspace(tmp_path)
        server = RecordingServer()
        register_tools(server, runtime)
        with pytest.raises(ValueError, match="Git"):
            await server.handlers["task_create"](workspace.id, "explain")
        task_id = await server.handlers["task_create"](
            workspace.id, "explain", execution_mode="read_only",
        )
        task = await runtime.get_task(task_id)
        assert task.execution_mode == "read_only"
        assert task.source == "mcp"
        assert task.actor.user_id == "mcp-client"
    finally:
        await runtime.stop()


async def test_read_only_rejects_unadvertised_global_tool_calls(tmp_path, authorized_read_host):
    class HiddenWrite:
        spec = ToolSpec(name="hidden_write", description="test", risk_level="low")
        invoked = False

        async def invoke(self, arguments):
            self.invoked = True
            return ToolResult.success("Unexpected write")

    class AdversarialModel:
        name = "adversarial"
        calls = 0

        async def chat(self, messages, *, tools=()):
            assert {tool.name for tool in tools} <= {"project_analyze", "workspace_query"}
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(content=None, tool_calls=(
                    ToolCall(id="hidden", name="hidden_write", arguments={}),
                ), finish_reason="tool_use", model=self.name)
            return LLMResponse(content="Done", finish_reason="stop", model=self.name)

    runtime, _ = _runtime_with_agent(tmp_path, AdversarialModel())
    tool = HiddenWrite()
    runtime.tools.register(tool)
    try:
        workspace = await runtime.open_workspace(tmp_path)
        selected = tmp_path / "app.py"
        selected.write_text("value = 1\n", encoding="utf-8")
        principal, scope = authorized_read_host(selected, source="test-host")
        task = await runtime.create_task(
            workspace.id, "analyze", execution_mode="read_only", actor=principal,
            source=principal.source, delegation_scope=scope,
        )
        result = await runtime.execute(task)
        assert result.status == TaskStatus.COMPLETED, result.error
        assert not tool.invoked, "Read-only scope fell back to the global tool registry"
    finally:
        await runtime.stop()
