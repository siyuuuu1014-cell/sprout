"""PA-2 A: unbound ProjectAnalyze tool calls fail closed before approvals/effects."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.agent.executor import ActionExecutor
from Sprout.events import TOOL_DENIED
from Sprout.llm.messages import ToolCall
from Sprout.message.models import Message
from Sprout.security.access import AccessDecision
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.security.layered_policy import LayeredPolicyEngine, PolicyLayer, PolicyRule
from Sprout.security.policy import SecurityPolicy
from Sprout.session.models import Session
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import DelegationScope
from Sprout.tests.conftest import build_runtime
from Sprout.tools import project_analyze_tool as module
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry, create_tool_registry
from Sprout.tools.result import ToolResult
from Sprout.tools.spec import ToolSpec
from Sprout.tools.workspace_query_tool import WorkspaceQueryTool
from Sprout.workspace.cache import WorkspaceAnalysisCache

SCOPE_REASON = "project_analysis_scope_unavailable"
NATIVE_REASON = "native_project_probe_unavailable"


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    effect = AsyncMock(side_effect=AssertionError("Unexpected external effect"))
    monkeypatch.setattr(module, "_system_tool_probe", effect)
    monkeypatch.setattr(module, "_run", effect)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", effect)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", effect)
    monkeypatch.setattr("Sprout.orchestration.terminal.ensure.ensure_temporal_async", effect)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    yield
    effect.assert_not_called()


def boundary(kind):
    if kind == "scope_deny":
        class ScopeDenied:
            def decide(self, request):
                return PolicyEngine().decide(replace(
                    request, scope=DelegationScope(denied_actions=frozenset({"tool.invoke"}))))
        return ScopeDenied()
    return LayeredPolicyEngine(organization=PolicyLayer("operator", [PolicyRule(
        action="tool.invoke",
        decision=AccessDecision.REQUIRE_APPROVAL if kind == "ask" else AccessDecision.ALLOW,
        id="pa2-policy",
    )]))


def effect_sentinels(monkeypatch, runtime):
    """All reachable service/cache entry points must remain untouched."""
    spies = []
    service = runtime._workspaces
    for owner, name in (
        (service, "open"), (service, "analyze_path"), (service, "analyze"),
        (service._intelligence, "analyze"), (runtime.storage.projects, "current_snapshot_id"),
        (runtime.storage.projects, "load_analysis"), (runtime.storage.projects, "save_analysis"),
        (WorkspaceAnalysisCache, "get"), (WorkspaceAnalysisCache, "persist"),
    ):
        spy = AsyncMock(side_effect=AssertionError("Analysis/storage/cache reached"))
        monkeypatch.setattr(owner, name, spy)
        spies.append(spy)
    for owner, name in ((type(service._scanner), "scan"),
                        (type(service._scanner), "build_read_plan"),
                        (type(service._scanner), "collect_resources"),
                        (WorkspaceAnalysisCache, "put")):
        spy = Mock(side_effect=AssertionError("Scanner/cache reached"))
        monkeypatch.setattr(owner, name, spy)
        spies.append(spy)
    path = Mock(side_effect=AssertionError("Path resolution/enumeration/read reached"))
    monkeypatch.setattr(module, "Path", path)
    spies.append(path)
    return spies


def make_tool(kind):
    registry = create_tool_registry()
    runtime, executor = build_runtime(tools=registry)
    if kind == "manual":
        tool = module.ProjectAnalyzeTool(analyze=AsyncMock())
        registry.register(tool)
    elif kind == "inherited":
        class Inherited(module.ProjectAnalyzeTool):
            pass
        tool = Inherited()
        registry.register(tool)
    elif kind == "override":
        class Override(module.ProjectAnalyzeTool):
            async def invoke(self, arguments):
                raise AssertionError("Subclass override executed")
        tool = Override()
        registry.register(tool)
    elif kind == "factory":
        tool = create_tool_registry().get("project_analyze")
        registry.register(tool)
    else:
        tool = registry.get("project_analyze")
    if kind == "overlay":
        executor = executor.with_scoped_tools({"project_analyze": tool}, strict=True)
    return runtime, executor, tool


@pytest.mark.parametrize("kind", [
    "factory", "manual", "runtime", "overlay", "inherited", "override",
])
@pytest.mark.parametrize("policy_kind", ["allow", "ask", "deny", "scope_deny"])
@pytest.mark.parametrize("source", ["interactive", "cron", "automation"])
@pytest.mark.parametrize("grant", [None, True, False])
async def test_unbound_analysis_denied_before_approval(
    monkeypatch, kind, policy_kind, source, grant,
):
    runtime, executor, tool = make_tool(kind)
    effects = effect_sentinels(monkeypatch, runtime)
    invoke = AsyncMock(side_effect=AssertionError("Tool.invoke reached"))
    monkeypatch.setattr(tool, "invoke", invoke)
    args = {
        "path": "/model-selected", "task_id": "forged", "workspace_id": "forged",
        "trusted": True, "force": True, "approval_granted": True,
        "scope": {"allowed_paths": ["/"]}, "metadata": {"trusted": True},
        "_sprout_context": {"task_id": "forged", "approval_granted": True},
    }
    store = MemoryOperationalStore()
    manager = ApprovalManager(store)
    if grant is not None:
        record = await manager.request(
            tool.spec.name, args, task_id="task", source=source, session_id="session",
            requested_by="actor", resource_scope="workspace", single_use=grant)
        await manager.decide(record.id, True)
    before = deepcopy(await store.list_approvals())
    approval_spies = []
    for owner, name in ((manager, "request"), (manager, "is_approved"),
                        (store, "save_approval"), (store, "save_approval_if_status")):
        spy = AsyncMock(wraps=getattr(owner, name))
        monkeypatch.setattr(owner, name, spy)
        approval_spies.append(spy)
    for name in ("mode_for", "asks_a_human"):
        spy = Mock(wraps=getattr(manager, name))
        monkeypatch.setattr(manager, name, spy)
        approval_spies.append(spy)
    executor.approvals = manager
    executor.policy_engine = boundary(policy_kind)
    executor.policy = SecurityPolicy(
        always_deny=frozenset({tool.spec.name}) if policy_kind == "deny" else frozenset())
    audit = Mock()
    events = SimpleNamespace(publish=AsyncMock())
    executor.audit, executor.events = audit, events
    results = await asyncio.gather(*(
        executor.execute(tool.spec.name, args, task_id="task", session_id="session",
                         source=source, requested_by="actor", resource_scope="workspace")
        for _ in range(3)
    ))
    policy_denied = policy_kind in {"deny", "scope_deny"}
    reason = ("Tool project_analyze is denied by the security policy"
              if policy_denied else SCOPE_REASON)
    assert all(not r.ok and r.error == reason and r.approval_id is None and r.data is None
               for r in results)
    assert await store.list_approvals() == before
    for spy in (*effects, *approval_spies, invoke):
        spy.assert_not_called()
    assert audit.record.call_count == 3
    audit_reason = ("DelegationScope" if policy_kind == "scope_deny"
                    else executor.policy.decide_for_spec(tool.spec).reason
                    if policy_kind == "deny" else SCOPE_REASON)
    assert all(call.args[1]["decision"] == "deny"
               and audit_reason in call.args[1]["reason"]
               for call in audit.record.call_args_list)
    assert events.publish.await_count == 3
    assert all(call.args[0].name == TOOL_DENIED
               and call.args[0].payload["reason"] ==
               ("policy" if policy_denied else "capability_unavailable")
               for call in events.publish.call_args_list)


@pytest.mark.parametrize("kind", ["default", "callback", "intelligence", "inherited"])
@pytest.mark.parametrize("flag", [None, False, True, 1, "true"])
async def test_direct_entry_rejects_before_path_or_analysis(monkeypatch, kind, flag):
    callback = AsyncMock(side_effect=AssertionError("Callback reached"))
    intelligence = SimpleNamespace(analyze=AsyncMock(side_effect=AssertionError("Analyze reached")))
    if kind == "inherited":
        class Derived(module.ProjectAnalyzeTool):
            pass
        tool = Derived(analyze=callback)
    else:
        tool = module.ProjectAnalyzeTool(
            analyze=callback if kind == "callback" else None,
            intelligence=intelligence if kind == "intelligence" else None)
    path = Mock(side_effect=AssertionError("Path reached before denial"))
    monkeypatch.setattr(module, "Path", path)
    args = {"path": "/forged", "trusted": True, "metadata": {"workspace_id": "fake"}}
    if flag is not None:
        args["include_system_tools"] = flag
    result = await tool.invoke(args)
    assert result.error == (NATIVE_REASON if flag is True else SCOPE_REASON)
    assert not result.ok and result.data is None and result.approval_id is None
    path.assert_not_called()
    callback.assert_not_called()
    intelligence.analyze.assert_not_called()


@pytest.mark.parametrize("flag", [None, False, True])
async def test_action_executor_standard_dispatch(monkeypatch, flag):
    runtime, executor, _ = make_tool("runtime")
    effects = effect_sentinels(monkeypatch, runtime)
    args = {"path": "/forged"}
    if flag is not None:
        args["include_system_tools"] = flag
    result = await ActionExecutor(executor).run_call(
        ToolCall(id="model", name="project_analyze", arguments=args),
        requested_by="actor", session_id="session")
    assert result.error == (NATIVE_REASON if flag is True else SCOPE_REASON)
    for spy in effects:
        spy.assert_not_called()


@pytest.mark.parametrize("initial", [False, True])
async def test_shared_arguments_cannot_change_negative_capability(monkeypatch, initial):
    runtime, executor, _ = make_tool("runtime")
    effects = effect_sentinels(monkeypatch, runtime)
    args = {"path": "/model", "include_system_tools": initial, "metadata": {"trusted": False}}

    class MutatingPolicy:
        def decide(self, request):
            args["include_system_tools"] = not initial
            args["metadata"]["trusted"] = True
            return PolicyEngine().decide(request)

    executor.policy_engine = MutatingPolicy()

    async def publish(event):
        args["path"] = "/another-workspace"
        args["scope"] = {"allowed_paths": ["/"]}
    executor.events = SimpleNamespace(publish=AsyncMock(side_effect=publish))
    result = await executor.execute("project_analyze", args)
    assert result.error == (NATIVE_REASON if initial else SCOPE_REASON)
    for spy in effects:
        spy.assert_not_called()


@pytest.mark.parametrize("entry", ["runtime", "overlay", "factory", "direct"])
@pytest.mark.parametrize("native", [False, True])
async def test_real_tool_and_runtime_have_zero_file_and_storage_access(
    monkeypatch, entry, native,
):
    storage = StorageBundle.in_memory()
    storage.metadata = open_metadata_store(":memory:")
    try:
        runtime, executor = build_runtime(storage=storage)
        tool = runtime.tools.get("project_analyze")
        if entry == "factory":
            tool = create_tool_registry().get("project_analyze")
            executor.tools.register(tool)
        if entry == "overlay":
            executor = executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
        effects = effect_sentinels(monkeypatch, runtime)
        metadata_write = AsyncMock(wraps=storage.metadata.save_workspace)
        monkeypatch.setattr(storage.metadata, "save_workspace", metadata_write)
        callback = AsyncMock(side_effect=AssertionError("Stored callback reached"))
        monkeypatch.setattr(tool, "_analyze", callback)
        io = Mock(side_effect=AssertionError("File metadata/read/enumeration reached"))
        # Confine global Path sentinels to the actual invocation, not pytest's I/O.
        with monkeypatch.context() as paths:
            for name in ("resolve", "is_dir", "iterdir", "rglob", "read_bytes", "read_text"):
                paths.setattr(Path, name, io)
            args = {"path": "/forged", "include_system_tools": native}
            result = (await tool.invoke(args) if entry == "direct"
                      else await executor.execute(tool.spec.name, args))
        assert result.error == (NATIVE_REASON if native else SCOPE_REASON)
        for spy in (*effects, metadata_write, callback, io):
            spy.assert_not_called()
    finally:
        storage.metadata.close()


@pytest.mark.parametrize("name", ["project_analyze", "unrelated_tool"])
async def test_unrelated_plugin_retains_normal_approval_flow(name):
    class Plugin:
        spec = ToolSpec(name=name, description="unrelated", input_schema={}, risk_level="high")
        invoke = AsyncMock(return_value=ToolResult.success("plugin executed"))
    registry = ToolRegistry()
    registry.register(Plugin())
    manager = ApprovalManager(MemoryOperationalStore())
    executor = ToolExecutor(registry, approvals=manager)
    result = await executor.execute(name, {"trusted": True}, task_id="plugin-task")
    assert result.approval_id and not Plugin.invoke.called
    await manager.decide(result.approval_id, True)
    result = await executor.execute(name, {"trusted": True}, task_id="plugin-task")
    assert result.ok
    Plugin.invoke.assert_awaited_once()


@pytest.fixture
def internal_runtime(tmp_path):
    root = tmp_path / "internal"
    root.mkdir()
    (root / "package.json").write_text('{"name":"internal-fixture"}', encoding="utf-8")
    (root / "main.py").write_text("def internal_symbol():\n    return 1\n", encoding="utf-8")
    storage = StorageBundle.in_memory()
    storage.metadata = open_metadata_store(":memory:")
    runtime, _ = build_runtime(storage=storage)
    yield runtime, root
    storage.metadata.close()


@pytest.mark.parametrize("entry", ["runtime", "service", "context", "query"])
async def test_explicit_internal_analysis_compatibility(internal_runtime, entry):
    runtime, root = internal_runtime
    workspace = await runtime.open_workspace(str(root))
    if entry == "runtime":
        analysis = await runtime.analyze_workspace(workspace.id)
        assert any(n.qualified_name == "internal_symbol" for n in analysis.graph.nodes)
    elif entry == "service":
        analysis = await runtime._workspaces.analyze(workspace.id)
        assert any(n.qualified_name == "internal_symbol" for n in analysis.graph.nodes)
    elif entry == "context":
        context = await runtime._contexts.build(
            message=Message(content="internal_symbol", user_id="fixture",
                            metadata={"workspace_id": workspace.id}),
            session=Session(id="internal-session", user_id="fixture"), tools={}, skills={})
        # A workspace hint alone cannot authorize project publication.
        assert context.workspace is None
        assert "workspace_context" not in context.metadata
    else:
        callback = AsyncMock(wraps=runtime.analyze_workspace)
        result = await WorkspaceQueryTool(callback).invoke(
            {"workspace_id": workspace.id, "operation": "symbols"})
        assert not result.ok
        assert result.error == "workspace_query_scope_unavailable"
        callback.assert_not_called()
