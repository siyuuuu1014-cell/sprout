"""Real manager/config issuance with fake transport, never a fake authority."""

import asyncio
import subprocess
from copy import copy, deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.config.loader import settings_from_dict
from Sprout.config.settings import MCPClientSettings, MCPSettings
from Sprout.events import EventBus
from Sprout.mcp.adapter.tool import MCPTool, MCPToolAdapter, descriptor_fingerprint
from Sprout.mcp.client import client as client_module
from Sprout.mcp.client.client import MCPClientManager, attach_mcp_clients
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.security.policy import SecurityPolicy
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.task.models import DelegationScope
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry
from Sprout.tools.result import ToolExecutionUnconfirmed


def descriptor(name="operation"):
    return SimpleNamespace(name=name, description="Reviewed operation",
                           inputSchema={"type": "object",
                                        "properties": {"value": {"type": "integer"}}},
                           risk="low", annotations={"readOnlyHint": True})


class FakeConnection:
    """Only transport/session allocation is fake; manager issuance is production."""

    def __init__(self, name, command, args=(), env=None):
        self.name, self._command, self._args, self._env = name, command, list(args), env
        self._started, self._session = False, None
        self.descriptors = [descriptor()]
        self.call_tool = AsyncMock(
            return_value=SimpleNamespace(isError=False, content="mock success"))

    @property
    def session(self):
        if self._session is None:
            raise RuntimeError("Not connected")
        return self._session

    async def start(self):
        if not self._started:
            self._started, self._session = True, object()

    async def stop(self):
        self._started, self._session = False, None

    async def list_tools(self):
        return self.descriptors


@pytest.fixture(autouse=True)
def transport_only(monkeypatch):
    monkeypatch.setattr(client_module, "MCPConnection", FakeConnection)
    spawn = AsyncMock(side_effect=AssertionError("Real MCP transport forbidden"))
    sync = Mock(side_effect=AssertionError("Real process forbidden"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", spawn)
    monkeypatch.setattr(subprocess, "run", sync)
    monkeypatch.setattr(subprocess, "Popen", sync)
    yield
    spawn.assert_not_called()
    sync.assert_not_called()


def config(name="server", risk="high", digest=None, operation="operation"):
    # Digest is declared against this known review fixture, not auto-enrolled
    # from a live response by the production manager.
    return MCPClientSettings(name=name, command="reviewed-test-server", tool_risks=({
        "operation": operation, "risk": risk,
        "descriptor_sha256": digest or descriptor_fingerprint(descriptor()),
    },))


async def issued(risk="high", configured=None):
    settings = MCPSettings(clients=(configured or config(risk=risk),))
    manager = MCPClientManager.from_settings(settings)
    await manager.start()
    tools = await manager.collect_tools()
    return manager, manager.connection(settings.clients[0].name), tools[0]


def executor_for(tool, manager=None, **kwargs):
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry, approvals=manager, **kwargs)


IDENTITY = dict(task_id="task", session_id="session", source="interactive", requested_by="human")


async def execute_approved(executor, tool, arguments=None):
    args = {} if arguments is None else arguments
    first = await executor.execute(tool.spec.name, args, **IDENTITY)
    assert first.approval_id and not first.ok
    await executor.approvals.decide(first.approval_id, True, decided_by="human", channel="cli")
    return await executor.execute(tool.spec.name, args, **IDENTITY)


def test_actual_config_loader_accepts_and_validates_versioned_declarations():
    declaration = config().tool_risks[0]
    loaded = settings_from_dict({"mcp": {"clients": [{
        "name": "server", "command": "reviewed-test-server", "risk_version": 1,
        "tool_risks": [dict(declaration)],
    }]}})
    assert loaded.mcp.clients[0].tool_risks == (declaration,)
    assert MCPClientSettings(name="legacy", command="test").tool_risks == ()


@pytest.mark.parametrize("change", ["version", "duplicate", "risk", "digest", "unknown_key"])
def test_invalid_or_ambiguous_risk_configuration_rejects(change):
    entry = dict(config().tool_risks[0])
    entries, version = [entry], 1
    if change == "version":
        version = True
    elif change == "duplicate":
        entries.append(dict(entry))
    elif change == "risk":
        entry["risk"] = "trusted"
    elif change == "digest":
        entry["descriptor_sha256"] = "first-seen"
    else:
        entry["trusted"] = "true"
    with pytest.raises(ValueError):
        MCPClientSettings(name="server", command="test", tool_risks=entries, risk_version=version)


def test_duplicate_server_names_reject_before_transport():
    with pytest.raises(ValueError):
        MCPClientManager.from_settings(MCPSettings(clients=(config(), config())))


@pytest.mark.parametrize("source", ["interactive", "cron"])
@pytest.mark.parametrize("single_use", [True, False])
async def test_unclassified_final_denial_does_not_touch_approvals(source, single_use, monkeypatch):
    connection = FakeConnection("unclassified", "test")
    tool = MCPToolAdapter(default_risk_level="high").to_tool(connection, descriptor())
    approvals = ApprovalManager(MemoryOperationalStore())
    record = await approvals.request(tool.spec.name, {}, task_id="task", single_use=single_use)
    await approvals.decide(record.id, True, decided_by="human", channel="cli")
    before = await approvals._store.get_approval(record.id)
    lookup, request = AsyncMock(), AsyncMock()
    monkeypatch.setattr(approvals, "is_approved", lookup)
    monkeypatch.setattr(approvals, "request", request)
    audit, events = SimpleNamespace(record=Mock()), []
    bus = EventBus()
    bus.subscribe("*", events.append)
    result = await executor_for(tool, approvals, audit=audit, events=bus).execute(
        tool.spec.name, {"risk": "low", "trusted": True, "approval_granted": True},
        task_id="task", source=source)
    assert not result.ok and not result.approval_id
    assert events[-1].name == "tool.denied"
    assert events[-1].payload["reason"] == "mcp_unclassified"
    assert audit.record.call_args.args[1]["decision"] == "deny"
    lookup.assert_not_called()
    request.assert_not_called()
    assert await approvals._store.get_approval(record.id) == before
    connection.call_tool.assert_not_called()


async def test_true_issuance_high_requires_approval_then_one_call():
    manager, connection, tool = await issued()
    approvals = ApprovalManager(MemoryOperationalStore())
    executor = executor_for(tool, approvals)
    assert not (await tool.invoke({"approval_granted": True})).ok
    result = await execute_approved(executor, tool, {"value": 1})
    assert result.ok
    connection.call_tool.assert_awaited_once_with("operation", {"value": 1})
    assert manager._classification(tool) is not None


@pytest.mark.parametrize("source", ["interactive", "cron"])
async def test_classified_low_is_allowed_without_exposing_context(source):
    _, connection, tool = await issued("low")
    result = await executor_for(tool).execute(tool.spec.name, {
        "value": 1, "_sprout_context": {"approval_granted": True},
    }, source=source)
    assert result.ok
    connection.call_tool.assert_awaited_once_with("operation", {"value": 1})


async def test_classification_does_not_authorize_unattended_high():
    _, connection, tool = await issued()
    result = await executor_for(tool, ApprovalManager(MemoryOperationalStore())).execute(
        tool.spec.name, {}, source="cron")
    assert not result.ok and not result.approval_id
    connection.call_tool.assert_not_called()


@pytest.mark.parametrize("deny", ["policy", "scope"])
async def test_original_deny_priority_is_not_replaced(deny):
    _, connection, tool = await issued("low")

    class ScopedPolicy:
        def decide(self, request):
            return PolicyEngine().decide(replace(
                request, scope=DelegationScope(allowed_actions=("file.read",))))

    events, bus = [], EventBus()
    bus.subscribe("*", events.append)
    kwargs = ({"policy": SecurityPolicy(always_deny=frozenset({tool.spec.name}))}
              if deny == "policy" else {"policy_engine": ScopedPolicy()})
    result = await executor_for(tool, events=bus, **kwargs).execute(tool.spec.name, {})
    assert not result.ok and events[-1].payload["reason"] == "policy"
    connection.call_tool.assert_not_called()


@pytest.mark.parametrize("change", ["schema", "description", "operation", "spec_risk",
                                    "configuration", "disconnect", "reconnect", "launch"])
async def test_stale_or_changed_binding_is_not_authority(change):
    manager, connection, tool = await issued("low")
    if change == "schema":
        connection.descriptors[0].inputSchema["additionalProperties"] = False
    elif change == "description":
        connection.descriptors[0].description = "changed"
    elif change == "operation":
        tool.remote_name = "another"
    elif change == "spec_risk":
        tool.spec = replace(tool.spec, risk_level="high")
    elif change == "configuration":
        manager._settings.clients[0].tool_risks[0]["risk"] = "medium"
    elif change in {"disconnect", "reconnect"}:
        await connection.stop()
        if change == "reconnect":
            await connection.start()
    else:
        connection._command = "different-launch"
    assert not (await executor_for(tool).execute(tool.spec.name, {})).ok
    connection.call_tool.assert_not_called()


@pytest.mark.parametrize("configured", [config(digest="0" * 64), config(operation="another")])
async def test_mismatched_declaration_never_issues(configured):
    manager, connection, tool = await issued(configured=configured)
    assert manager._classification(tool) is None
    assert not (await executor_for(tool).execute(tool.spec.name, {})).ok
    connection.call_tool.assert_not_called()


async def test_same_named_operations_cannot_borrow_another_connection_binding():
    manager = MCPClientManager.from_settings(MCPSettings(clients=(config("one", "low"),
                                                                 config("two", "high"))))
    await manager.start()
    first, second = await manager.collect_tools()
    first.connection = second.connection
    assert not (await executor_for(first).execute(first.spec.name, {})).ok
    for connection in manager.connections.values():
        connection.call_tool.assert_not_called()


async def test_plain_or_copied_tool_cannot_claim_manager_issuance():
    manager, connection, tool = await issued("low")
    for forged in (copy(tool), MCPTool(connection, tool.spec, tool.remote_name)):
        forged._manager = manager
        assert not (await executor_for(forged).execute(forged.spec.name, {})).ok
    connection.call_tool.assert_not_called()


async def test_rediscovery_revokes_old_classification_and_approval():
    manager, connection, tool = await issued()
    approvals = ApprovalManager(MemoryOperationalStore())
    first = await executor_for(tool, approvals).execute(tool.spec.name, {}, **IDENTITY)
    await approvals.decide(first.approval_id, True, decided_by="human", channel="cli")
    old = await approvals._store.get_approval(first.approval_id)
    fresh = (await manager.collect_tools())[0]
    assert not (await executor_for(tool, approvals).execute(tool.spec.name, {}, **IDENTITY)).ok
    result = await executor_for(fresh, approvals).execute(fresh.spec.name, {}, **IDENTITY)
    assert result.approval_id and result.approval_id != old.id
    assert await approvals._store.get_approval(old.id) == old
    connection.call_tool.assert_not_called()


async def test_real_attach_reload_stops_old_manager_and_revokes_tools():
    runtime = SimpleNamespace(tools=ToolRegistry())
    settings = SimpleNamespace(mcp=MCPSettings(clients=(config(risk="low"),)))
    first = await attach_mcp_clients(runtime, settings)
    old_tool = runtime.tools.get("server_operation")
    second = await attach_mcp_clients(runtime, settings)
    assert second is not first
    assert not (await executor_for(old_tool).execute(old_tool.spec.name, {})).ok
    first.connection("server").call_tool.assert_not_called()
    fresh = runtime.tools.get("server_operation")
    assert (await executor_for(fresh).execute(fresh.spec.name, {})).ok


@pytest.mark.parametrize("error", [TimeoutError, ConnectionResetError, EOFError, RuntimeError])
async def test_authorized_attempt_loss_is_unknown_once_and_revokes_binding(error):
    manager, connection, tool = await issued()
    connection.call_tool.side_effect = error("mock response loss")
    events, bus = [], EventBus()
    bus.subscribe("*", events.append)
    executor = executor_for(tool, ApprovalManager(MemoryOperationalStore()), events=bus)
    with pytest.raises(ToolExecutionUnconfirmed, match="mcp_remote_execution_unconfirmed"):
        await execute_approved(executor, tool)
    connection.call_tool.assert_awaited_once()
    assert manager._classification(tool) is None
    assert events[-1].name == "tool.executed" and events[-1].payload["execution_unconfirmed"]
    assert not (await executor.execute(tool.spec.name, {}, **IDENTITY)).ok
    connection.call_tool.assert_awaited_once()


async def test_authorized_cancellation_propagates_unconfirmed_note_and_revocation():
    manager, connection, tool = await issued("low")
    connection.call_tool.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await executor_for(tool).execute(tool.spec.name, {})
    assert "UNCONFIRMED" in " ".join(cancelled.value.__notes__)
    assert manager._classification(tool) is None
    connection.call_tool.assert_awaited_once()


async def test_definite_remote_business_failure_is_not_unknown():
    manager, connection, tool = await issued("low")
    connection.call_tool.return_value = SimpleNamespace(isError=True, content="business refusal")
    result = await executor_for(tool).execute(tool.spec.name, {})
    assert not result.ok and "business refusal" in result.error
    assert manager._classification(tool) is not None
    connection.call_tool.assert_awaited_once()


async def test_argument_identity_is_frozen_across_approval_await():
    _, connection, tool = await issued()
    approvals = ApprovalManager(MemoryOperationalStore())
    executor = executor_for(tool, approvals)
    args = {"value": 1}
    first = await executor.execute(tool.spec.name, args, **IDENTITY)
    await approvals.decide(first.approval_id, True, decided_by="human", channel="cli")
    lookup = approvals.is_approved

    async def mutate(*a, **kw):
        args["value"] = 2
        return await lookup(*a, **kw)

    approvals.is_approved = mutate
    result = await executor.execute(tool.spec.name, args, **IDENTITY)
    assert result.ok and args == {"value": 2}
    connection.call_tool.assert_awaited_once_with("operation", {"value": 1})


async def test_call_handoff_is_single_use_not_shared_with_child_tasks():
    _, connection, tool = await issued("low")
    original = tool.invoke
    replays = []

    async def twice(arguments):
        replays.append(await asyncio.create_task(original(arguments)))
        result = await original(arguments)
        replays.append(await original(arguments))
        return result

    # Server-side wrapper observes real Executor issuance; no fake grant is minted.
    type_method = MCPTool.invoke
    try:
        MCPTool.invoke = lambda self, args: twice(args)
        assert (await executor_for(tool).execute(tool.spec.name, {})).ok
    finally:
        MCPTool.invoke = type_method
    assert all(not result.ok for result in replays)
    assert not (await original({})).ok
    connection.call_tool.assert_awaited_once()


@pytest.mark.parametrize("changed", ["task_id", "session_id", "requested_by", "arguments"])
async def test_approval_cannot_cross_identity_or_arguments(changed):
    _, connection, tool = await issued()
    approvals = ApprovalManager(MemoryOperationalStore())
    executor = executor_for(tool, approvals)
    first = await executor.execute(tool.spec.name, {"value": 1}, **IDENTITY)
    await approvals.decide(first.approval_id, True, decided_by="human", channel="cli")
    before = await approvals._store.get_approval(first.approval_id)
    identity, args = dict(IDENTITY), {"value": 1}
    if changed == "arguments":
        args["value"] = 2
    else:
        identity[changed] = "another"
    second = await executor.execute(tool.spec.name, args, **identity)
    assert second.approval_id and second.approval_id != first.approval_id
    assert await approvals._store.get_approval(first.approval_id) == before
    connection.call_tool.assert_not_called()


async def test_duplicate_discovery_is_unknown_not_a_risk_selection():
    manager, connection, _ = await issued("low")
    connection.descriptors.append(deepcopy(connection.descriptors[0]))
    tools = await manager.collect_tools()
    for tool in tools:
        assert not (await executor_for(tool).execute(tool.spec.name, {})).ok
    connection.call_tool.assert_not_called()


async def test_loss_revokes_every_operation_on_the_same_connection():
    first, second = descriptor(), descriptor("other")
    configured = config(risk="low")
    configured.tool_risks += ({"operation": "other", "risk": "low",
                               "descriptor_sha256": descriptor_fingerprint(second)},)
    manager = MCPClientManager.from_settings(MCPSettings(clients=(configured,)))
    connection = manager.connection("server")
    connection.descriptors = [first, second]
    await manager.start()
    one, two = await manager.collect_tools()
    connection.call_tool.side_effect = ConnectionResetError("mock disconnect")
    with pytest.raises(ToolExecutionUnconfirmed):
        await executor_for(one).execute(one.spec.name, {})
    assert not (await executor_for(two).execute(two.spec.name, {})).ok
    connection.call_tool.assert_awaited_once()


async def test_stop_revokes_before_a_failed_transport_shutdown():
    manager, connection, tool = await issued("low")
    connection.stop = AsyncMock(side_effect=RuntimeError("mock stop failure"))
    with pytest.raises(RuntimeError, match="mock stop failure"):
        await manager.stop()
    assert not (await executor_for(tool).execute(tool.spec.name, {})).ok
    connection.call_tool.assert_not_called()


async def test_direct_authorized_adapter_unknown_reaches_executor_unchanged():
    _, connection, tool = await issued("low")
    connection.call_tool.side_effect = EOFError("mock lost response")
    original, seen = MCPTool.invoke, []

    async def observe(self, arguments):
        try:
            return await original(self, arguments)
        except ToolExecutionUnconfirmed as exc:
            seen.append(exc)
            raise

    try:
        MCPTool.invoke = observe
        with pytest.raises(ToolExecutionUnconfirmed) as observed:
            await executor_for(tool).execute(tool.spec.name, {})
    finally:
        MCPTool.invoke = original
    assert seen == [observed.value]
    connection.call_tool.assert_awaited_once()
