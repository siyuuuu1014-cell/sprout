"""Real approval consumption and broker decisions; fake only final process I/O."""

import asyncio
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.config.defaults import default_settings
from Sprout.execution.process_broker import ProcessBroker
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.security.layered_policy import LayeredPolicyEngine
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.tools import context_keys, system_tools
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry
from Sprout.tools.system_tools import CliRunTool
from Sprout.workspace.models import Workspace, WorkspaceKind


class _ObservedPolicy:
    def __init__(self):
        self.engine = LayeredPolicyEngine.from_settings(
            default_settings().security, base=PolicyEngine(),
        )
        self.decisions = []

    def decide(self, request):
        decision = self.engine.decide(request)
        self.decisions.append(decision.decision)
        return decision


@pytest.fixture
def probe(tmp_path, monkeypatch):
    """Self-contained real broker/policy fixture; only final OS I/O is fake."""
    policy = _ObservedPolicy()
    approvals = ApprovalManager(MemoryOperationalStore())
    broker = ProcessBroker(policy, approvals=approvals)
    run = AsyncMock(wraps=broker.run)
    monkeypatch.setattr(broker, "run", run)
    spawn = AsyncMock(return_value=SimpleNamespace(
        returncode=0, communicate=AsyncMock(return_value=(b"mock-only\n", b"")),
    ))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(system_tools.shutil, "which",
                        lambda command: "git" if command == "git" else sys.executable)
    fallback = AsyncMock(side_effect=AssertionError("Host fallback forbidden"))
    monkeypatch.setattr(system_tools, "_run_command", fallback)
    tool = CliRunTool(allowed_roots=(tmp_path,), process_broker=broker)
    registry = ToolRegistry()
    registry.register(tool)
    return SimpleNamespace(
        policy=policy, approvals=approvals, broker=broker, run=run, spawn=spawn,
        tool=tool, registry=registry, fallback=fallback, root=tmp_path,
    )


def arguments(probe, source, claim=None):
    context = dict(task_id="audit-task", session_id="audit-session",
                   requested_by="untrusted-caller", source=source)
    if claim is not None:
        context["approval_granted"] = claim
    return {"command": "audit-python", "args": ["-c", "print('mock-only')"],
            "cwd": str(probe.root), "_sprout_context": context}


def executor_and_call(probe):
    executor = ToolExecutor(probe.registry, approvals=probe.approvals)
    supplied = arguments(probe, "interactive", True)
    attribution = dict(task_id="server-task", session_id="server-session",
                       source="interactive", requested_by="server-owner", resource_scope="root")
    return executor, supplied, attribution


async def approve(probe, executor, supplied, attribution):
    pending = await executor.execute("cli_tool_run", supplied, **attribution)
    assert not pending.ok and pending.approval_id
    probe.spawn.assert_not_awaited()
    await probe.approvals.decide(pending.approval_id, True, decided_by="human", channel="cli")


@pytest.mark.parametrize("source", ["interactive", "cron", "automation"])
@pytest.mark.parametrize("has_manager", [False, True])
async def test_legacy_broker_bool_is_never_approval(probe, source, has_manager):
    if not has_manager:
        probe.broker._approvals = None
    outcome = await probe.broker.run(
        Workspace(id="probe", root=probe.root, kind=WorkspaceKind.LOCAL_DIRECTORY),
        [sys.executable, "-c", "print('mock-only')"], cwd=probe.root,
        upstream_approval_granted=True, source=source,
    )
    assert not outcome.allowed
    probe.spawn.assert_not_awaited()


@pytest.mark.parametrize("claim", ["token", "approval-id", {"trusted": True}, 1])
async def test_caller_token_id_or_truthy_object_cannot_authorize(probe, claim):
    supplied = arguments(probe, "interactive", claim)
    supplied.update(trusted_local=True, approval_id="caller-selected", token="caller-token")
    assert not (await probe.tool.invoke(supplied)).ok
    probe.spawn.assert_not_awaited()


@pytest.mark.parametrize("changed", [
    "command", "args", "cwd", "env", "timeout", "task", "session", "source",
    "requester", "resource_scope", "secret_names", "required_env", "scope", "actor",
    "workspace_id", "workspace_kind", "human_source",
])
async def test_real_handoff_rejects_changed_execution_subject(probe, monkeypatch, changed):
    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    original = probe.broker.run
    alternate = probe.root / "other"
    alternate.mkdir()

    async def tamper(workspace, command, **kwargs):
        command = list(command)
        if changed == "command":
            command[0] = "different-python"
        elif changed == "args":
            command[-1] = "different-code"
        elif changed == "cwd":
            kwargs["cwd"] = str(alternate)
        elif changed == "env":
            kwargs["env"] = {**kwargs["env"], "NEW_VALUE": "different"}
        elif changed == "timeout":
            kwargs["timeout_seconds"] += 1
        elif changed in {"task", "session"}:
            kwargs[changed + "_id"] = "different"
        elif changed == "source":
            kwargs["source"] = "cron"
        elif changed == "human_source":
            kwargs["source"] = "mcp_client"
        elif changed == "workspace_id":
            workspace = replace(workspace, id="different")
        elif changed == "workspace_kind":
            workspace = replace(workspace, kind=WorkspaceKind.GIT_REPOSITORY)
        elif changed == "requester":
            kwargs["requested_by"] = "different"
        elif changed == "resource_scope":
            kwargs["resource_scope"] = "different"
        elif changed in {"secret_names", "required_env"}:
            kwargs[changed] = ("NOT_APPROVED",)
        elif changed == "scope":
            from Sprout.task.models import DelegationScope

            kwargs["scope"] = DelegationScope()
        elif changed == "actor":
            from Sprout.gateway.identity import Principal

            kwargs["actor"] = Principal(user_id="different")
        return await original(workspace, command, **kwargs)

    monkeypatch.setattr(probe.broker, "run", tamper)
    assert not (await executor.execute("cli_tool_run", supplied, **attribution)).ok
    probe.run.assert_awaited_once()
    assert probe.policy.decisions[-1].value == "require_approval"
    probe.spawn.assert_not_awaited()


async def test_expired_call_handoff_is_refused(probe, monkeypatch):
    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    monkeypatch.setattr(context_keys, "_CLI_HANDOFF_TTL", 0)
    assert not (await executor.execute("cli_tool_run", supplied, **attribution)).ok
    probe.spawn.assert_not_awaited()
    assert context_keys._CLI_HANDOFF.get() is None


async def test_expired_persisted_approval_never_mints_handoff(probe):
    executor, supplied, attribution = executor_and_call(probe)
    record = await probe.approvals.request("cli_tool_run", supplied, ttl_seconds=-1, **attribution)
    await probe.approvals.decide(record.id, True, decided_by="human", channel="cli")
    assert not (await executor.execute("cli_tool_run", supplied, **attribution)).ok
    probe.spawn.assert_not_awaited()
    assert context_keys._CLI_HANDOFF.get() is None


async def test_one_prompt_and_no_reuse_of_consumed_single_use_record(probe):
    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    result = await executor.execute("cli_tool_run", supplied, **attribution)
    assert result.ok and not result.approval_id
    probe.spawn.assert_awaited_once()
    assert context_keys._CLI_HANDOFF.get() is None
    repeated = await executor.execute("cli_tool_run", supplied, **attribution)
    assert not repeated.ok and repeated.approval_id
    probe.spawn.assert_awaited_once()


async def test_same_call_handoff_is_consumed_once_and_dead_after_return(probe, monkeypatch):
    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    original = probe.broker.run
    captured = []

    async def twice(*args, **kwargs):
        captured.append((args, kwargs))
        first = await original(*args, **kwargs)
        assert first.allowed
        assert not (await original(*args, **kwargs)).allowed
        return first

    monkeypatch.setattr(probe.broker, "run", twice)
    assert (await executor.execute("cli_tool_run", supplied, **attribution)).ok
    args, kwargs = captured[0]
    assert not (await original(*args, **kwargs)).allowed
    probe.spawn.assert_awaited_once()


async def test_child_task_cannot_inherit_parent_handoff_authority(probe, monkeypatch):
    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    original = probe.broker.run

    async def child_then_parent(*args, **kwargs):
        child = await asyncio.create_task(original(*args, **kwargs))
        assert not child.allowed
        return await original(*args, **kwargs)

    monkeypatch.setattr(probe.broker, "run", child_then_parent)
    assert (await executor.execute("cli_tool_run", supplied, **attribution)).ok
    probe.spawn.assert_awaited_once()


async def test_two_concurrent_executor_calls_share_only_one_single_use_approval(probe):
    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    results = await asyncio.gather(
        executor.execute("cli_tool_run", supplied, **attribution),
        executor.execute("cli_tool_run", supplied, **attribution),
    )
    assert sum(result.ok for result in results) == 1
    probe.spawn.assert_awaited_once()
    assert context_keys._CLI_HANDOFF.get() is None


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError, asyncio.CancelledError])
async def test_handoff_expires_after_aborted_dispatch(probe, monkeypatch, failure):
    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    original = probe.broker.run
    captured = []

    async def abort(*args, **kwargs):
        captured.append((args, kwargs))
        raise failure("simulated interruption")

    monkeypatch.setattr(probe.broker, "run", abort)
    if failure is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            await executor.execute("cli_tool_run", supplied, **attribution)
    else:
        assert not (await executor.execute("cli_tool_run", supplied, **attribution)).ok
    assert context_keys._CLI_HANDOFF.get() is None
    args, kwargs = captured[0]
    assert not (await original(*args, **kwargs)).allowed
    probe.spawn.assert_not_awaited()


async def test_real_handoff_never_overrides_current_policy_deny(probe, monkeypatch):
    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    original = probe.broker.run

    async def deny(workspace, command, **kwargs):
        return await original(workspace, ["git", "push", "--force"], **kwargs)

    monkeypatch.setattr(probe.broker, "run", deny)
    assert not (await executor.execute("cli_tool_run", supplied, **attribution)).ok
    assert probe.policy.decisions[-1].value == "deny"
    probe.spawn.assert_not_awaited()


async def test_full_executable_path_with_spaces_stays_one_argv_item(probe, monkeypatch):
    executable = str(probe.root / "Trusted Python" / "python.exe")
    monkeypatch.setattr(system_tools.shutil, "which", lambda command: executable)
    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    assert (await executor.execute("cli_tool_run", supplied, **attribution)).ok
    assert probe.spawn.call_args.args == (executable, "-c", "print('mock-only')")


@pytest.mark.parametrize("different", ["broker", "approvals"])
async def test_handoff_cannot_cross_broker_or_approval_manager(probe, monkeypatch, different):
    from Sprout.execution.process_broker import ProcessBroker
    from Sprout.security.approval import ApprovalManager
    from Sprout.storage.local.memory import MemoryOperationalStore

    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    original = probe.broker.run

    async def redirect(*args, **kwargs):
        if different == "broker":
            other = ProcessBroker(probe.policy, approvals=probe.approvals)
            return await other.run(*args, **kwargs)
        probe.broker._approvals = ApprovalManager(MemoryOperationalStore())
        return await original(*args, **kwargs)

    monkeypatch.setattr(probe.broker, "run", redirect)
    assert not (await executor.execute("cli_tool_run", supplied, **attribution)).ok
    probe.spawn.assert_not_awaited()


async def test_caller_cannot_mutate_arguments_while_approval_is_consumed(probe, monkeypatch):
    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    original = probe.approvals.is_approved

    async def mutate(*args, **kwargs):
        accepted = await original(*args, **kwargs)
        supplied["args"].append("not-approved")
        return accepted

    monkeypatch.setattr(probe.approvals, "is_approved", mutate)
    assert (await executor.execute("cli_tool_run", supplied, **attribution)).ok
    assert probe.spawn.call_args.args == (sys.executable, "-c", "print('mock-only')")


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError, asyncio.CancelledError])
async def test_final_os_io_failure_consumes_handoff_without_retry(probe, failure):
    executor, supplied, attribution = executor_and_call(probe)
    await approve(probe, executor, supplied, attribution)
    child = probe.spawn.return_value
    child.stdout = child.stderr = None
    from unittest.mock import AsyncMock

    child.wait = AsyncMock(return_value=0)
    child.communicate.side_effect = failure("mock final I/O failure")
    if failure is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            await executor.execute("cli_tool_run", supplied, **attribution)
    else:
        assert not (await executor.execute("cli_tool_run", supplied, **attribution)).ok
    probe.spawn.assert_awaited_once()
    assert context_keys._CLI_HANDOFF.get() is None
    args, kwargs = probe.run.call_args
    assert not (await probe.broker.run(*args, **kwargs)).allowed
    probe.spawn.assert_awaited_once()
    probe.fallback.assert_not_awaited()


async def test_two_distinct_concurrent_approved_calls_keep_independent_handoffs(probe):
    executor, supplied, first = executor_and_call(probe)
    second = {**first, "task_id": "other-task", "session_id": "other-session"}
    await approve(probe, executor, supplied, first)
    await approve(probe, executor, supplied, second)
    results = await asyncio.gather(
        executor.execute("cli_tool_run", supplied, **first),
        executor.execute("cli_tool_run", supplied, **second),
    )
    assert all(result.ok for result in results)
    assert probe.spawn.await_count == 2
    assert context_keys._CLI_HANDOFF.get() is None
