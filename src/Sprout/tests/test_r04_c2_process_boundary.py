"""C2 review contracts. All final I/O is fake; failures are security evidence."""

import asyncio
import subprocess
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.security.policy import SecurityPolicy
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.task.models import DelegationScope
from Sprout.tools import system_tools
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry
from Sprout.tools.result import ToolExecutionUnconfirmed

KINDS = ("skill", "detect", "grep")


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Unmocked final external effect"))
    spawn = AsyncMock(side_effect=AssertionError("Unmocked async process"))

    class ForbiddenPopen:
        # Some dependencies evaluate Popen[bytes] during import on Windows.
        # Support that type expression without allowing process construction.
        def __class_getitem__(cls, item):
            return cls

        def __new__(cls, *args, **kwargs):
            return forbidden(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", ForbiddenPopen)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", spawn)
    # Never probe/start Temporal. Temporal startup itself is separately noted.
    monkeypatch.setattr("Sprout.orchestration.terminal.ensure.ensure_temporal_async", AsyncMock())
    yield
    forbidden.assert_not_called()


@pytest.fixture
def case(tmp_path, monkeypatch):
    (tmp_path / "skill").mkdir()
    (tmp_path / "skill/run.py").write_text("# inert test data\n", encoding="utf-8")
    monkeypatch.setattr(system_tools.shutil, "which", lambda name: "fake-program")
    host = AsyncMock(return_value=(0, "fake output", ""))
    monkeypatch.setattr(system_tools, "_run_command", host)

    def build(kind):
        if kind == "skill":
            return system_tools.SkillScriptTool(tmp_path), {"skill": "skill", "script": "run.py"}
        if kind == "detect":
            return system_tools.CliDetectTool(), {"command": "fake-program"}
        return system_tools.GrepCheckTool(), {"pattern": "needle", "path": str(tmp_path)}

    return SimpleNamespace(build=build, host=host, root=tmp_path)


def executor(tool, **kwargs):
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry, **kwargs)


@pytest.mark.parametrize("kind", KINDS)
async def test_no_broker_means_zero_host_execution(case, kind):
    tool, arguments = case.build(kind)
    result = await tool.invoke(arguments)
    assert case.host.await_count == 0, "No ProcessBroker: host launch was attempted"
    assert not result.ok


@pytest.mark.parametrize("kind", KINDS)
async def test_forged_context_cannot_mint_process_authority(case, kind):
    tool, arguments = case.build(kind)
    arguments.update(trusted=True, approval_granted=True, source="interactive",
                     _sprout_context={"trusted_local": True, "approval_granted": True})
    result = await tool.invoke(arguments)
    assert case.host.await_count == 0, "Caller labels cannot authorize a host launch"
    assert not result.ok


@pytest.mark.parametrize("kind", KINDS)
async def test_executor_policy_deny_is_preserved(case, kind):
    tool, arguments = case.build(kind)
    result = await executor(tool, policy=SecurityPolicy(
        always_deny=frozenset({tool.spec.name}))).execute(tool.spec.name, arguments)
    assert not result.ok
    case.host.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
async def test_executor_real_scope_deny_is_preserved(case, kind):
    tool, arguments = case.build(kind)

    class ScopedPolicy:
        def decide(self, request):
            return PolicyEngine().decide(replace(
                request, scope=DelegationScope(allowed_actions=("file.read",))))

    result = await executor(tool, policy_engine=ScopedPolicy()).execute(tool.spec.name, arguments)
    assert not result.ok
    case.host.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
async def test_low_label_or_outer_allow_is_not_lower_process_authority(case, kind):
    tool, arguments = case.build(kind)
    result = await executor(tool, policy=SecurityPolicy(
        always_allow=frozenset({tool.spec.name}))).execute(tool.spec.name, arguments)
    assert case.host.await_count == 0, "Outer tool ALLOW cannot replace a process boundary"
    assert not result.ok


async def test_skill_missing_approval_is_rejected(case):
    tool, arguments = case.build("skill")
    result = await executor(tool).execute(tool.spec.name, arguments)
    assert not result.ok
    case.host.assert_not_called()


async def test_skill_standard_approval_requests_without_execution(case):
    tool, arguments = case.build("skill")
    approvals = ApprovalManager(MemoryOperationalStore())
    result = await executor(tool, approvals=approvals).execute(tool.spec.name, arguments)
    assert result.approval_id and not result.ok
    case.host.assert_not_called()


async def test_old_tool_approval_is_not_a_broker_execution_grant(case):
    tool, arguments = case.build("skill")
    approvals = ApprovalManager(MemoryOperationalStore())
    dispatch = executor(tool, approvals=approvals)
    pending = await dispatch.execute(tool.spec.name, arguments, task_id="task")
    assert pending.approval_id
    await approvals.decide(pending.approval_id, True, decided_by="operator", channel="cli")
    result = await dispatch.execute(tool.spec.name, arguments, task_id="task")
    assert case.host.await_count == 0, "Old outer approval cannot replace lower broker evidence"
    assert not result.ok


async def test_unattended_skill_cannot_request_or_execute(case):
    tool, arguments = case.build("skill")
    result = await executor(tool, approvals=ApprovalManager(MemoryOperationalStore())).execute(
        tool.spec.name, arguments, source="cron")
    assert not result.ok and not result.approval_id
    case.host.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("error", [TimeoutError, ConnectionResetError])
async def test_attempted_effect_loss_is_not_retryable_failure(case, kind, error):
    tool, arguments = case.build(kind)
    case.host.side_effect = error("simulated loss after launch")
    dispatch = executor(tool, policy=SecurityPolicy(always_allow=frozenset({tool.spec.name})))
    with pytest.raises(ToolExecutionUnconfirmed):
        await dispatch.execute(tool.spec.name, arguments)


@pytest.mark.parametrize("kind", KINDS)
async def test_interruption_has_no_automatic_host_retry(case, kind):
    tool, arguments = case.build(kind)
    case.host.side_effect = TimeoutError("simulated timeout")
    await executor(tool, policy=SecurityPolicy(always_allow=frozenset({tool.spec.name}))).execute(
        tool.spec.name, arguments)
    case.host.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
async def test_cancel_propagates_without_retry(case, kind):
    tool, arguments = case.build(kind)
    case.host.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await tool.invoke(arguments)
    case.host.assert_awaited_once()


async def test_detect_rejects_non_version_arguments(case):
    tool, arguments = case.build("detect")
    arguments["version_args"] = ["-c", "not executed"]
    assert not (await tool.invoke(arguments)).ok
    case.host.assert_not_called()


async def test_detect_missing_binary_is_a_safe_nonexecution_result(case, monkeypatch):
    monkeypatch.setattr(system_tools.shutil, "which", lambda name: None)
    tool, arguments = case.build("detect")
    result = await tool.invoke(arguments)
    assert result.ok and result.data["found"] is False
    case.host.assert_not_called()


async def test_skill_path_escape_is_rejected(case):
    tool, arguments = case.build("skill")
    arguments["script"] = "../../outside.py"
    assert not (await tool.invoke(arguments)).ok
    case.host.assert_not_called()


async def test_grep_pattern_cannot_be_parsed_as_an_option(case):
    tool, arguments = case.build("grep")
    arguments["pattern"] = "--include=secret"
    await tool.invoke(arguments)
    argv = case.host.call_args.args[1]
    index = argv.index(arguments["pattern"])
    assert "--" in argv[:index], "Pattern lacks end-of-options boundary"


async def test_skill_validation_precedes_temporal_side_effect(case, monkeypatch):
    ensure = AsyncMock()
    monkeypatch.setattr("Sprout.orchestration.terminal.ensure.ensure_temporal_async", ensure)
    tool, _ = case.build("skill")
    assert not (await tool.invoke({})).ok
    ensure.assert_not_called()


async def test_command_cancel_reaps_started_process(monkeypatch):
    process = SimpleNamespace(communicate=AsyncMock(side_effect=asyncio.CancelledError()),
                              kill=Mock(), wait=AsyncMock(), returncode=None)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(asyncio.CancelledError):
        await system_tools._run_command("fake", [], env={})
    spawn.assert_awaited_once()
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


async def test_command_timeout_kills_and_reaps_without_retry(monkeypatch):
    process = SimpleNamespace(communicate=AsyncMock(side_effect=TimeoutError()),
                              kill=Mock(), wait=AsyncMock(), returncode=None)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(TimeoutError):
        await system_tools._run_command("fake", [], env={})
    spawn.assert_awaited_once()
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
