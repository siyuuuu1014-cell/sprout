"""Approval preservation for disabled Skill types across registration paths."""

import asyncio
import socket
import subprocess
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.security.policy import SecurityPolicy
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.task.models import DelegationScope
from Sprout.tools import system_tools
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry, create_tool_registry
from Sprout.tools.result import ToolResult


@pytest.fixture(autouse=True)
def forbid_effects(monkeypatch):
    spawn = AsyncMock(side_effect=AssertionError("External execution forbidden"))
    sync = Mock(side_effect=AssertionError("External execution forbidden"))

    class ForbiddenPopen:
        def __class_getitem__(cls, item):
            return cls

        def __new__(cls, *args, **kwargs):
            return sync(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", ForbiddenPopen)
    monkeypatch.setattr(subprocess, "run", sync)
    monkeypatch.setattr(socket.socket, "connect", sync)
    monkeypatch.setattr(socket, "getaddrinfo", sync)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", spawn)
    monkeypatch.setattr(system_tools, "_run_command", spawn)
    monkeypatch.setattr("Sprout.orchestration.terminal.ensure.ensure_temporal_async", spawn)
    yield
    spawn.assert_not_called()
    sync.assert_not_called()


@pytest.mark.parametrize("source", ["interactive", "cron", "automation"])
@pytest.mark.parametrize("policy_kind", ["allow", "ask", "deny", "scope_deny"])
@pytest.mark.parametrize("grant", [None, True, False])
@pytest.mark.parametrize("kind", ["factory", "direct", "subclass", "override", "cross",
                                  "overlay", "renamed"])
async def test_disabled_builtin_preserves_all_approval_state(
    tmp_path, monkeypatch, source, policy_kind, grant, kind,
):
    registry = create_tool_registry(skills_dir=tmp_path)
    tool = registry.get("skill_script")

    class Derived(system_tools.SkillScriptTool):
        pass

    class Override(system_tools.SkillScriptTool):
        async def invoke(self, arguments):
            raise AssertionError("Subclass override reached")

    if kind in {"direct", "subclass", "override"}:
        cls = {"direct": system_tools.SkillScriptTool,
               "subclass": Derived, "override": Override}[kind]
        tool = cls(tmp_path)
        registry.register(tool)
    if kind == "cross":
        registry = ToolRegistry()
        registry.register(tool)
    name = "renamed" if kind == "renamed" else "skill_script"
    if kind == "renamed":
        tool.spec = replace(tool.spec, name=name)
        registry.register(tool)
    invoke = AsyncMock(side_effect=AssertionError("Disabled invoke reached"))
    monkeypatch.setattr(tool, "invoke", invoke)
    args = {"skill": "x", "script": "x.py", "force": True, "trusted": True,
            "approval_granted": True, "metadata": {"capability": "enabled"},
            "_sprout_context": {"trusted_local": True, "approval_granted": True}}
    store = MemoryOperationalStore()
    approvals = ApprovalManager(store)
    if grant is not None:
        record = await approvals.request(name, args, task_id="task", single_use=grant)
        await approvals.decide(record.id, True)
    before = deepcopy(await store.list_approvals())
    request = AsyncMock(wraps=approvals.request)
    consume = AsyncMock(wraps=approvals.is_approved)
    save = AsyncMock(wraps=store.save_approval)
    cas = AsyncMock(wraps=store.save_approval_if_status)
    monkeypatch.setattr(approvals, "request", request)
    monkeypatch.setattr(approvals, "is_approved", consume)
    monkeypatch.setattr(store, "save_approval", save)
    monkeypatch.setattr(store, "save_approval_if_status", cas)
    policy = SecurityPolicy(
        always_allow=frozenset({name}) if policy_kind == "allow" else frozenset(),
        always_deny=frozenset({name}) if policy_kind == "deny" else frozenset(),
    )

    class Boundary:
        def decide(self, action):
            if policy_kind == "scope_deny":
                action = replace(action, scope=DelegationScope(allowed_actions=("file.read",)))
            return PolicyEngine().decide(action)

    boundary = Mock(wraps=Boundary())
    executor = ToolExecutor(registry, policy=policy, policy_engine=boundary, approvals=approvals)
    if kind == "overlay":
        registry = ToolRegistry()
        executor = ToolExecutor(registry, policy=policy, policy_engine=boundary,
                                approvals=approvals).with_scoped_tools({name: tool}, strict=True)
    results = await asyncio.gather(*(
        executor.execute(name, args, task_id="task", source=source) for _ in range(4)
    ))
    assert all(not r.ok and not r.approval_id for r in results)
    expected = (f"Tool {name} is denied by the security policy"
                if policy_kind in {"deny", "scope_deny"}
                else "skill_script_execution_unavailable")
    assert all(r.error == expected for r in results)
    assert boundary.decide.call_count == 4
    assert await store.list_approvals() == before
    for spy in (request, consume, save, cas, invoke):
        spy.assert_not_called()


@pytest.mark.parametrize("kind", ["plugin", "replacement", "overlay"])
async def test_unbound_instances_retain_ordinary_approval_flow(tmp_path, kind):
    registry = create_tool_registry(skills_dir=tmp_path)

    class Plugin:
        spec = registry.get("skill_script").spec
        invoke = AsyncMock(return_value=ToolResult.success("plugin"))

    tool = Plugin()
    if kind != "overlay":
        registry.register(tool)
    store = MemoryOperationalStore()
    approvals = ApprovalManager(store)
    executor = ToolExecutor(registry, approvals=approvals)
    if kind == "overlay":
        executor = executor.with_scoped_tools({"skill_script": tool}, strict=True)
    pending = await executor.execute("skill_script", {}, task_id="task")
    assert pending.approval_id
    await approvals.decide(pending.approval_id, True)
    result = await executor.execute("skill_script", {}, task_id="task")
    assert (await store.get_approval(pending.approval_id)).status.value == "consumed"
    assert result.ok
    tool.invoke.assert_awaited_once()


async def test_bound_identity_survives_spec_rename_and_scoped_dispatch(tmp_path, monkeypatch):
    registry = create_tool_registry(skills_dir=tmp_path)
    builtin = registry.get("skill_script")
    builtin.spec = replace(builtin.spec, name="renamed")
    builtin.invoke = AsyncMock(side_effect=AssertionError("Disabled invoke reached"))
    approvals = ApprovalManager(MemoryOperationalStore())
    request = AsyncMock(wraps=approvals.request)
    consume = AsyncMock(wraps=approvals.is_approved)
    monkeypatch.setattr(approvals, "request", request)
    monkeypatch.setattr(approvals, "is_approved", consume)
    executor = ToolExecutor(registry, approvals=approvals).with_scoped_tools(
        {"renamed": builtin}, strict=True,
    )
    result = await executor.execute("renamed", {})
    assert result.error == "skill_script_execution_unavailable"
    for spy in (request, consume, builtin.invoke):
        spy.assert_not_called()


async def test_disabled_type_remains_denied_across_registries(tmp_path):
    first = create_tool_registry(skills_dir=tmp_path)
    builtin = first.get("skill_script")
    second = ToolRegistry()
    second.register(builtin)
    approvals = ApprovalManager(MemoryOperationalStore())
    result = await ToolExecutor(second, approvals=approvals).execute("skill_script", {})
    assert result.error == "skill_script_execution_unavailable"
    assert await approvals.store.list_approvals() == []
