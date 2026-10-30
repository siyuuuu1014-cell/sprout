"""Real Runtime authority with controlled tools; no model or host effects."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.security.access import AccessDecision, ActionType
from Sprout.tests.test_r04_pa2_b1_binding import node_scope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry
from Sprout.tools.result import ToolResult
from Sprout.tools.spec import ToolSpec


class Tool:
    spec = ToolSpec(name="controlled_pure_tool", description="Pure test", input_schema={})

    def __init__(self):
        self.invoke = AsyncMock(return_value=ToolResult.success("actual controlled result"))


@pytest.mark.parametrize("case", [
    "valid", "expired", "actor", "cross-task", "copied", "missing", "binding-changed"])
async def test_runtime_subject_precedes_generic_tool_policy_and_approval(rig, case):
    tool = Tool()
    rig.task = replace(rig.task, delegation_scope=replace(rig.task.delegation_scope,
        allowed_paths=(), denied_paths=()))
    await rig.storage.metadata.save_task(rig.task)
    decisions = []
    original = rig.runtime._policy_engine.decide

    def decide(request):
        decisions.append(request)
        return original(request)

    rig.runtime._policy_engine.decide = decide
    approvals = AsyncMock()
    executor = ToolExecutor(ToolRegistry(), scoped={tool.spec.name: tool}, scoped_only=True,
                            policy_engine=rig.runtime._policy_engine, approvals=approvals)
    with node_scope(rig) as scope:
        rig.runtime._node_tool_scopes[tool] = scope
        current = rig.task
        if case == "expired":
            current = replace(current, delegation_scope=replace(current.delegation_scope,
                expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        if case == "actor":
            current = replace(current, actor=replace(current.actor, user_id="other"))
        if case == "binding-changed":
            current = replace(current, metadata={**current.metadata, "host_acceptance_v1": {}})
        if case == "cross-task":
            rig.runtime._node_tool_scopes[tool] = object()
        await rig.storage.metadata.save_task(current)
        call = executor.execute(tool.spec.name, {"trusted": True, "task_id": "forged"})
        if case == "copied":
            result = await asyncio.create_task(call)
        elif case == "missing":
            rig.runtime._node_tool_scopes.pop(tool)
            result = await call
        else:
            result = await call
    assert result.ok is (case == "valid")
    assert tool.invoke.await_count == (1 if case == "valid" else 0)
    approvals.is_approved.assert_not_called()
    approvals.request.assert_not_called()
    if case == "valid":
        subject = next(r for r in decisions if r.action is ActionType.TOOL_INVOKE)
        assert subject.actor == rig.principal and subject.task_id == rig.task.id
        assert original(subject).decision is AccessDecision.ALLOW


@pytest.mark.parametrize("write_frozen", [False, True])
async def test_broker_attribution_and_frozen_write_refusal(rig, monkeypatch, write_frozen):
    from Sprout.execution.file_broker import FileBroker
    from Sprout.execution.models import SandboxRef
    from Sprout.execution.sandbox_tool import SandboxReadTool, SandboxWriteTool
    from Sprout.orchestration.models import NodeStatus, NodeType

    root = rig.workspace.root
    rig.task = replace(rig.task, delegation_scope=replace(rig.task.delegation_scope,
        allowed_actions=frozenset({"tool.invoke", "file.read", "file.write"})),
        metadata={**rig.task.metadata, "host_acceptance_v1": {
            "resources": {"src/frozen.py": {}}}})
    await rig.storage.metadata.save_task(rig.task)
    sandbox = SandboxRef(id="controlled", kind="git_worktree", root=root)
    monkeypatch.setattr(rig.storage.metadata, "list_execution_nodes", AsyncMock(return_value=[
        SimpleNamespace(type=NodeType.SANDBOX, status=NodeStatus.COMPLETED,
                        metadata={"output": {"root": str(root), "id": sandbox.id}})]))
    broker = FileBroker(rig.runtime._policy_engine)
    broker._access = AsyncMock(return_value=(True, "controlled file content"))
    cls = SandboxWriteTool if write_frozen else SandboxReadTool
    tool = cls(sandbox, broker, task_id=rig.task.id, scope=rig.task.delegation_scope)
    decisions = []
    original = rig.runtime._policy_engine.decide

    def decide(request):
        decisions.append(request)
        return original(request)

    monkeypatch.setattr(rig.runtime._policy_engine, "decide", decide)
    executor = ToolExecutor(ToolRegistry(), scoped={tool.spec.name: tool}, scoped_only=True,
                            policy_engine=rig.runtime._policy_engine)
    with node_scope(rig) as scope:
        rig.runtime._node_tool_scopes[tool] = scope
        result = await executor.execute(tool.spec.name,
            {"path": "src/frozen.py", "content": "tampered", "_sprout_context": {
                "actor": "forged", "task_id": "forged"}})
    if write_frozen:
        assert not result.ok
        broker._access.assert_not_awaited()
    else:
        assert result.ok and broker._access.await_count == 1
        assert all(r.actor == rig.principal and r.task_id == rig.task.id for r in decisions)
