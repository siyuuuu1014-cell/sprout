"""Native project probes are unavailable, before effects or approval use."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.events import TOOL_DENIED
from Sprout.security.access import AccessDecision
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.security.layered_policy import LayeredPolicyEngine, PolicyLayer, PolicyRule
from Sprout.security.policy import SecurityPolicy
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.task.models import DelegationScope
from Sprout.tests.conftest import build_runtime
from Sprout.tools import project_analyze_tool as module
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry, create_tool_registry
from Sprout.tools.result import ToolResult

REASON = "native_project_probe_unavailable"


@pytest.fixture(autouse=True)
def no_probe(monkeypatch):
    effect = AsyncMock(side_effect=AssertionError("Unexpected external effect"))
    scan = Mock(side_effect=AssertionError("Unexpected native discovery or fallback scan"))
    monkeypatch.setattr(module, "_system_tool_probe", effect)
    monkeypatch.setattr(module, "_run", effect)
    monkeypatch.setattr(module, "_estimate_lines_from_rg_output", scan)
    monkeypatch.setattr(module.shutil, "which", scan)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", effect)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", effect)
    monkeypatch.setattr("Sprout.orchestration.terminal.ensure.ensure_temporal_async", effect)
    yield
    effect.assert_not_called()
    scan.assert_not_called()


def boundary(kind):
    class Scoped:
        def decide(self, request):
            return PolicyEngine().decide(replace(
                request, scope=DelegationScope(allowed_actions=("file.read",))))

    if kind == "scope_deny":
        return Scoped()
    return LayeredPolicyEngine(organization=PolicyLayer("operator", [PolicyRule(
        action="tool.invoke",
        decision=AccessDecision.REQUIRE_APPROVAL if kind == "ask" else AccessDecision.ALLOW,
        id="project-policy",
    )]))


@pytest.mark.parametrize("kind", ["manual", "factory", "runtime", "overlay", "subclass"])
@pytest.mark.parametrize("source", ["interactive", "cron", "automation"])
@pytest.mark.parametrize("policy_kind", ["allow", "ask", "deny", "scope_deny"])
@pytest.mark.parametrize("grant", [None, True, False])
async def test_probe_denied_before_approval_and_invoke(
    tmp_path, monkeypatch, kind, source, policy_kind, grant,
):
    class Derived(module.ProjectAnalyzeTool):
        async def invoke(self, arguments):
            raise AssertionError("Override must not execute")

    registry = create_tool_registry()
    if kind == "runtime":
        _, assembly = build_runtime(tools=registry)
        tool = assembly.tools.get("project_analyze")
        assert tool._analyze is not None
    elif kind == "factory":
        tool = registry.get("project_analyze")
    else:
        tool = Derived() if kind == "subclass" else module.ProjectAnalyzeTool()
        registry.register(tool)
    invoke = AsyncMock(side_effect=AssertionError("invoke reached"))
    monkeypatch.setattr(tool, "invoke", invoke)
    args = {"path": str(tmp_path), "include_system_tools": True, "trusted": True,
            "force": True, "approval_granted": True, "metadata": {"capability": True},
            "_sprout_context": {"approval_granted": True}}
    store = MemoryOperationalStore()
    approvals = ApprovalManager(store)
    if grant is not None:
        record = await approvals.request(tool.spec.name, args, task_id="task", single_use=grant)
        await approvals.decide(record.id, True)
    before = deepcopy(await store.list_approvals())
    spies = []
    for owner, name in ((approvals, "request"), (approvals, "is_approved"),
                        (store, "save_approval"), (store, "save_approval_if_status")):
        spy = AsyncMock(wraps=getattr(owner, name))
        monkeypatch.setattr(owner, name, spy)
        spies.append(spy)
    audit = Mock()
    events = SimpleNamespace(publish=AsyncMock())
    executor = ToolExecutor(registry, approvals=approvals, audit=audit, events=events,
                            policy_engine=boundary(policy_kind), policy=SecurityPolicy(
                                always_deny=frozenset({tool.spec.name})
                                if policy_kind == "deny" else frozenset()))
    if kind == "overlay":
        executor = executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
    results = await asyncio.gather(*(
        executor.execute(tool.spec.name, args, task_id="task", source=source) for _ in range(3)
    ))
    expected = ("Tool project_analyze is denied by the security policy"
                if policy_kind in {"deny", "scope_deny"} else REASON)
    assert all(not r.ok and not r.approval_id and r.error == expected for r in results)
    assert await store.list_approvals() == before
    for spy in (*spies, invoke):
        spy.assert_not_called()
    assert audit.record.call_count == 3
    assert all(c.args[1]["decision"] == "deny" for c in audit.record.call_args_list)
    assert events.publish.await_count == 3
    assert all(c.args[0].name == TOOL_DENIED for c in events.publish.call_args_list)
    if policy_kind in {"allow", "ask"}:
        assert all(c.args[1]["reason"] == REASON for c in audit.record.call_args_list)


async def test_direct_probe_denial_precedes_path_and_analysis(monkeypatch):
    analyze = AsyncMock(side_effect=AssertionError("Analysis before refusal"))
    tool = module.ProjectAnalyzeTool(analyze=analyze)
    scan = Mock(side_effect=AssertionError("Path before refusal"))
    with monkeypatch.context() as paths:
        paths.setattr(module.Path, "resolve", scan)
        paths.setattr(module.Path, "is_dir", scan)
        result = await tool.invoke({"include_system_tools": True})
    assert result.error == REASON and not result.ok
    analyze.assert_not_called()
    scan.assert_not_called()


@pytest.mark.parametrize("flag", [None, False, 1, "true"])
async def test_ordinary_analysis_output_is_preserved(tmp_path, flag):
    analysis = SimpleNamespace(
        workspace=SimpleNamespace(id="test"),
        manifest=SimpleNamespace(detected_languages=("python",),
                                 framework_hints=(), entry_points=(),
                                 test_commands=(), build_commands=()),
        read_plan=SimpleNamespace(resources=()), graph=SimpleNamespace(nodes=(), edges=()),
        knowledge=SimpleNamespace(items=()),
    )
    analyze = AsyncMock(return_value=analysis)
    args = {"path": str(tmp_path)}
    if flag is not None:
        args["include_system_tools"] = flag
    result = await module.ProjectAnalyzeTool(analyze=analyze).invoke(args)
    assert result.ok and result.data["languages"] == ["python"]
    assert "system_tools" not in result.data
    analyze.assert_awaited_once()


@pytest.mark.parametrize("plugin", [False, True])
@pytest.mark.parametrize("flag", [None, False])
async def test_ordinary_or_unrelated_plugin_keeps_approval_flow(tmp_path, plugin, flag):
    tool = module.ProjectAnalyzeTool()
    if plugin:
        tool = SimpleNamespace(spec=tool.spec, invoke=AsyncMock(return_value=ToolResult.success()))
    else:
        tool.invoke = AsyncMock(return_value=ToolResult.success())
    registry = ToolRegistry()
    registry.register(tool)
    approvals = ApprovalManager(MemoryOperationalStore())
    dispatch = ToolExecutor(registry, approvals=approvals, policy_engine=boundary("ask"))
    args = {"path": str(tmp_path), "include_system_tools": True if plugin else flag}
    first = await dispatch.execute(tool.spec.name, args, task_id="task")
    assert first.approval_id
    await approvals.decide(first.approval_id, True)
    assert (await dispatch.execute(tool.spec.name, args, task_id="task")).ok
    assert (await approvals.store.get_approval(first.approval_id)).status.value == "consumed"
    tool.invoke.assert_awaited_once()


async def test_caller_mutation_during_approval_cannot_change_frozen_request(tmp_path):
    args = {"path": str(tmp_path), "include_system_tools": False, "metadata": {"x": []}}
    tool = module.ProjectAnalyzeTool()
    tool.invoke = AsyncMock(return_value=ToolResult.success())
    registry = ToolRegistry()
    registry.register(tool)
    approvals = ApprovalManager(MemoryOperationalStore())

    async def mutate(*unused, **kwargs):
        args["include_system_tools"] = True
        args["metadata"]["x"].append("changed")
        return True

    approvals.is_approved = AsyncMock(side_effect=mutate)
    executor = ToolExecutor(registry, approvals=approvals, policy_engine=boundary("ask"))
    result = await executor.execute(tool.spec.name, args)
    assert result.ok
    received = tool.invoke.await_args.args[0]
    assert received["include_system_tools"] is False
    assert received["metadata"] == {"x": []}


@pytest.mark.parametrize("policy_kind", ["allow", "ask", "deny", "scope_deny"])
@pytest.mark.parametrize("flag", [None, False])
async def test_non_probe_request_retains_policy_outcome(tmp_path, policy_kind, flag):
    tool = module.ProjectAnalyzeTool()
    tool.invoke = AsyncMock(return_value=ToolResult.success("ordinary"))
    registry = ToolRegistry()
    registry.register(tool)
    approvals = ApprovalManager(MemoryOperationalStore())
    args = {"path": str(tmp_path)}
    if flag is not None:
        args["include_system_tools"] = flag
    executor = ToolExecutor(registry, approvals=approvals, policy_engine=boundary(policy_kind),
                            policy=SecurityPolicy(always_deny=frozenset({tool.spec.name})
                                                  if policy_kind == "deny" else frozenset()))
    result = await executor.execute(tool.spec.name, args)
    assert result.ok == (policy_kind == "allow")
    assert bool(result.approval_id) == (policy_kind == "ask")
    assert tool.invoke.await_count == (1 if policy_kind == "allow" else 0)
