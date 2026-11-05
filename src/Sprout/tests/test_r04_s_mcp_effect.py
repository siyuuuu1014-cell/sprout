"""MCP Task authority before approvals; transport is fake, issuer is real."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from Sprout.mcp.client import client as client_module
from Sprout.security.approval import ApprovalManager
from Sprout.tests.test_r04_c1_mcp_trusted_wiring import FakeConnection, issued
from Sprout.tests.test_r04_pa2_b1_binding import node_scope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry


@pytest.mark.parametrize("case", [
    "deny", "expired", "actor", "source", "owner", "session", "paths"])
@pytest.mark.parametrize("risk", ["low", "high"])
@pytest.mark.parametrize("single_use", [True, False])
async def test_current_task_refuses_before_any_approval_or_remote(
    rig, monkeypatch, case, risk, single_use,
):
    monkeypatch.setattr(client_module, "MCPConnection", FakeConnection)
    manager, connection, tool = await issued(risk)
    approvals = ApprovalManager(rig.storage.operational)
    existing = await approvals.request(tool.spec.name, {}, task_id=rig.task.id,
                                       single_use=single_use)
    await approvals.decide(existing.id, True)
    before = deepcopy(await rig.storage.operational.list_approvals())
    spies = []
    for owner, name in ((approvals, "request"), (approvals, "is_approved"),
                        (rig.storage.operational, "save_approval"),
                        (rig.storage.operational, "save_approval_if_status")):
        spy = AsyncMock(wraps=getattr(owner, name))
        monkeypatch.setattr(owner, name, spy)
        spies.append(spy)
    current = rig.task
    if case == "deny":
        current = replace(current, delegation_scope=replace(
            current.delegation_scope, denied_actions=frozenset({"tool.invoke"})))
    if case == "expired":
        current = replace(current, delegation_scope=replace(current.delegation_scope,
            expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    if case == "actor":
        current = replace(current, actor=replace(current.actor, user_id="other"))
    if case == "source":
        current = replace(current, source="forged")
    if case == "session":
        current = replace(current, metadata={"session_id": "forged"})
    if case == "owner":
        rig.node = replace(rig.node, metadata={"execution_guard": {"owner": "copied-owner"}})
    await rig.storage.metadata.save_task(current)
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry, approvals=approvals,
        policy_engine=rig.runtime._policy_engine, scoped={tool.spec.name: tool}, scoped_only=True)
    try:
        with node_scope(rig):
            result = await executor.execute(tool.spec.name,
                {"trusted": True, "metadata": {"scope": "unrestricted"}},
                task_id="forged", source="forged", requested_by="forged")
        assert not result.ok
        connection.call_tool.assert_not_called()
        for spy in spies:
            spy.assert_not_called()
        assert await rig.storage.operational.list_approvals() == before
    finally:
        await manager.stop()


@pytest.mark.parametrize("copied", [False, True])
async def test_valid_action_only_scope_and_copied_context(rig, monkeypatch, copied):
    monkeypatch.setattr(client_module, "MCPConnection", FakeConnection)
    manager, connection, tool = await issued("low")
    # Explicit trusted host grant for an opaque operation, not file access.
    # No path is invented to authorize a path-restricted Task.
    rig.task = replace(rig.task, delegation_scope=replace(rig.task.delegation_scope,
        allowed_paths=(), denied_paths=(), allowed_actions=frozenset({"tool.invoke"})))
    await rig.storage.metadata.save_task(rig.task)
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry, policy_engine=rig.runtime._policy_engine,
        scoped={tool.spec.name: tool}, scoped_only=True)
    try:
        with node_scope(rig):
            call = executor.execute(tool.spec.name, {}, task_id="not-authority")
            result = await asyncio.create_task(call) if copied else await call
        assert result.ok is not copied
        assert connection.call_tool.await_count == (0 if copied else 1)
    finally:
        await manager.stop()


async def test_current_valid_task_preserves_real_classified_approval_handoff(rig, monkeypatch):
    monkeypatch.setattr(client_module, "MCPConnection", FakeConnection)
    manager, connection, tool = await issued("high")
    rig.task = replace(rig.task, delegation_scope=replace(rig.task.delegation_scope,
        allowed_paths=(), denied_paths=(), allowed_actions=frozenset({"tool.invoke"})))
    await rig.storage.metadata.save_task(rig.task)
    registry = ToolRegistry()
    registry.register(tool)
    approvals = ApprovalManager(rig.storage.operational)
    executor = ToolExecutor(registry, approvals=approvals,
        policy_engine=rig.runtime._policy_engine, scoped={tool.spec.name: tool}, scoped_only=True)
    identity = dict(task_id="model-task", session_id="model-session", source="model-source",
                    requested_by=rig.principal.user_id)
    try:
        with node_scope(rig):
            pending = await executor.execute(tool.spec.name, {}, **identity)
            assert not pending.ok and pending.approval_id
            record = next(r for r in await rig.storage.operational.list_approvals()
                          if r.id == pending.approval_id)
            assert record.task_id == rig.task.id and record.session_id == rig.session.id
            connection.call_tool.assert_not_called()
            await approvals.decide(pending.approval_id, True)
            result = await executor.execute(tool.spec.name, {}, **identity)
        assert result.ok and connection.call_tool.await_count == 1
    finally:
        await manager.stop()
