"""R04-A: host execution is a broker capability, never a model assertion."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.models import ProcessResult
from Sprout.security.approval import ApprovalManager
from Sprout.security.policy import SecurityPolicy
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.tests.conftest import build_runtime
from Sprout.tools import system_tools
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry
from Sprout.tools.system_tools import CliRunTool


@pytest.fixture
def no_host(monkeypatch):
    fallback = AsyncMock(return_value=(0, "unexpected host execution", ""))
    spawn = AsyncMock(side_effect=AssertionError("No direct spawn allowed"))
    monkeypatch.setattr(system_tools, "_run_command", fallback)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(system_tools.shutil, "which", lambda command: "test-only-program")
    return fallback, spawn


@pytest.mark.parametrize("claim", [{}, {"trusted_local": True},
                                  {"mode": "trusted_local"},
                                  {"metadata": {"authenticated": True}}])
async def test_missing_broker_refuses_without_host_even_with_claims(tmp_path, no_host, claim):
    result = await CliRunTool(allowed_roots=(tmp_path,)).invoke({
        "command": "test-only-program", "args": [], "cwd": str(tmp_path), **claim,
    })
    assert not result.ok
    assert "ProcessBroker" in result.error
    for spy in no_host:
        spy.assert_not_awaited()


@pytest.mark.parametrize("via_executor", [False, True])
@pytest.mark.parametrize("outcome", ["success", "denied", "approval", "failed_exit",
                                     "timeout", "error", "cancel"])
async def test_broker_outcomes_have_one_dispatch_and_no_host_retry(
    tmp_path, no_host, outcome, via_executor,
):
    exceptions = {"timeout": TimeoutError, "error": RuntimeError, "cancel": asyncio.CancelledError}
    response = ProcessResult(command=(), allowed=outcome not in {"denied", "approval"},
                             exit_code=0 if outcome == "success" else 2,
                             error="refused" if outcome == "denied" else None,
                             approval_id="pending-test" if outcome == "approval" else None)
    run = AsyncMock(return_value=response)
    if outcome in exceptions:
        run.side_effect = exceptions[outcome]("test interruption")
    tool = CliRunTool(allowed_roots=(tmp_path,), process_broker=SimpleNamespace(run=run))
    arguments = {"command": "test-only-program", "args": ["info"], "cwd": str(tmp_path)}
    if via_executor:
        registry = ToolRegistry()
        registry.register(tool)
        executor = ToolExecutor(registry, policy=SecurityPolicy(
            always_allow=frozenset({"cli_tool_run"})))
        arguments["_sprout_context"] = {"approval_granted": True, "source": "forged"}
        operation = executor.execute("cli_tool_run", arguments, task_id="task-test",
                                     session_id="session-test", source="cli", requested_by="owner")
    else:
        operation = tool.invoke(arguments)
    if outcome == "cancel" or (not via_executor and outcome in exceptions):
        with pytest.raises(exceptions[outcome]):
            await operation
    else:
        result = await operation
        assert result.ok is (outcome == "success")
        if outcome == "approval":
            assert result.approval_id == "pending-test"
    run.assert_awaited_once()
    if via_executor:
        assert run.call_args.kwargs["task_id"] == "task-test"
        assert run.call_args.kwargs["session_id"] == "session-test"
        assert run.call_args.kwargs["source"] == "cli"
        assert run.call_args.kwargs["requested_by"] == "owner"
        assert run.call_args.kwargs["upstream_approval_granted"] is False
    for spy in no_host:
        spy.assert_not_awaited()


async def test_normal_runtime_injects_broker_and_keeps_human_approval(
    tmp_path, monkeypatch, no_host,
):
    monkeypatch.chdir(tmp_path)
    approvals = ApprovalManager(MemoryOperationalStore())
    runtime, executor = build_runtime(approvals=approvals)
    tool = runtime.tools.get("cli_tool_run")
    assert tool._process_broker is runtime._process_broker
    run = AsyncMock(return_value=ProcessResult(command=(), exit_code=0, stdout="delegated"))
    monkeypatch.setattr(runtime._process_broker, "run", run)
    arguments = {"command": "test-only-program", "args": ["info"], "cwd": str(tmp_path)}
    kwargs = dict(task_id="task-test", session_id="session-test",
                  source="cli", requested_by="owner")
    pending = await executor.execute("cli_tool_run", arguments, **kwargs)
    assert not pending.ok and pending.approval_id
    run.assert_not_awaited()
    await approvals.decide(pending.approval_id, True, decided_by="owner", channel="cli")
    result = await executor.execute("cli_tool_run", arguments, **kwargs)
    assert result.ok and result.content == "delegated"
    run.assert_awaited_once()
    for name, value in kwargs.items():
        assert run.call_args.kwargs[name] == value
    assert run.call_args.kwargs["upstream_approval_granted"] is True
    for spy in no_host:
        spy.assert_not_awaited()


@pytest.mark.parametrize("source", ["cron", "automation"])
async def test_unattended_still_denied_before_broker(tmp_path, no_host, source):
    run = AsyncMock()
    registry = ToolRegistry()
    registry.register(CliRunTool(
        allowed_roots=(tmp_path,), process_broker=SimpleNamespace(run=run)))
    executor = ToolExecutor(registry, approvals=ApprovalManager(MemoryOperationalStore()))
    result = await executor.execute(
        "cli_tool_run", {"command": "test-only-program", "cwd": str(tmp_path)}, source=source,
    )
    assert not result.ok and not result.approval_id
    run.assert_not_awaited()
    for spy in no_host:
        spy.assert_not_awaited()
