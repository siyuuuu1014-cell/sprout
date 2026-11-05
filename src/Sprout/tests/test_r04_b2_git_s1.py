"""S1 mock mechanics are NOT proof of a trusted installation or managed view."""

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.git_broker import GitBroker
from Sprout.execution.proc import OutputLimitExceeded
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.security.engine import PolicyEngine
from Sprout.task.models import DelegationScope
from Sprout.tools import system_tools
from Sprout.tools.result import ToolExecutionUnconfirmed
from Sprout.tools.system_tools import GitInspectTool
from Sprout.workspace.models import Workspace, WorkspaceKind


@pytest.fixture(autouse=True)
def no_real_effects(monkeypatch):
    spawn = AsyncMock(side_effect=AssertionError("Real child forbidden"))
    legacy = AsyncMock(return_value=(0, "unsafe mocked success", ""))
    host = AsyncMock(side_effect=AssertionError("Host fallback forbidden"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("Sprout.execution.git_broker.run_capture", legacy)
    monkeypatch.setattr(system_tools, "_run_git", host)
    yield SimpleNamespace(spawn=spawn, legacy=legacy, host=host)
    host.assert_not_called()


@pytest.mark.parametrize("operation", ["status", "diff"])
async def test_public_read_denies_unattested_workspace(tmp_path, no_real_effects, operation):
    workspace = Workspace(id="claimed-trusted", root=tmp_path, kind=WorkspaceKind.GIT_REPOSITORY)
    result = await getattr(GitBroker(PolicyEngine()), operation)(workspace)
    assert not result.allowed
    assert result.reason == "s1_trusted_installation_unavailable"
    no_real_effects.spawn.assert_not_called()
    no_real_effects.legacy.assert_not_called()


@pytest.fixture
def s1():
    return importlib.import_module("Sprout.execution.git_s1")


@pytest.mark.parametrize("operation", ["status", "diff"])
async def test_missing_managed_view_is_not_inferred_from_path(
    tmp_path, monkeypatch, s1, no_real_effects, operation,
):
    # A mocked authority seam; no production issuer exists in this change.
    installation = s1._Installation(Path("C:/trusted/git.exe"), "mock-version", "mock-identity")
    monkeypatch.setattr(s1, "_resolve_installation", lambda: installation)
    workspace = Workspace(id="test", root=tmp_path)
    result = await s1.run_read(workspace, operation, DelegationScope())
    assert not result.allowed and result.reason == "s1_managed_view_unavailable"
    no_real_effects.spawn.assert_not_called()


@pytest.mark.parametrize("operation", ["status", "diff"])
async def test_policy_deny_precedes_s1(tmp_path, monkeypatch, s1, operation):
    class Deny:
        def decide(self, request):
            return PolicyDecision(AccessDecision.DENY, "test deny")

    run = AsyncMock()
    monkeypatch.setattr(s1, "run_read", run)
    result = await getattr(GitBroker(Deny()), operation)(Workspace(id="test", root=tmp_path))
    assert not result.allowed
    run.assert_not_called()


def fake_process(code=0, stdout=b"mock output", stderr=b""):
    out, err = asyncio.StreamReader(), asyncio.StreamReader()
    out.feed_data(stdout)
    out.feed_eof()
    err.feed_data(stderr)
    err.feed_eof()
    return SimpleNamespace(returncode=code, stdout=out, stderr=err,
                           wait=AsyncMock(return_value=code))


@pytest.fixture
def mock_plan(tmp_path, s1):
    # Isolated fake plan tests runner mechanics only, never production trust.
    return s1._Plan(
        installation=s1._Installation(tmp_path / "trusted git.exe", "mock", "mock"),
        view=tmp_path / "managed", home=tmp_path / "home", hooks=tmp_path / "empty-hooks",
        config=tmp_path / "empty-config", temp=tmp_path / "temp",
        system_env={"SYSTEMROOT": "C:/Windows"},
    )


@pytest.mark.parametrize("operation", ["status", "diff"])
async def test_fixed_argv_and_private_environment(
    monkeypatch, s1, mock_plan, no_real_effects, operation,
):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "99")
    monkeypatch.setenv("GIT_SSH_COMMAND", "forbidden")
    monkeypatch.setenv("API_KEY", "secret-not-for-child")
    no_real_effects.spawn.return_value = fake_process()
    no_real_effects.spawn.side_effect = None
    result = await s1._execute(mock_plan, operation)
    assert result.allowed and result.output == "mock output"
    args, kwargs = no_real_effects.spawn.call_args
    assert args[0] == str(mock_plan.installation.path)
    assert "--no-pager" in args and "--no-optional-locks" in args
    assert f"core.hooksPath={mock_plan.hooks}" in args
    assert "core.fsmonitor=false" in args and "protocol.allow=never" in args
    assert "diff.ignoreSubmodules=all" in args
    if operation == "diff":
        assert "--no-ext-diff" in args and "--no-textconv" in args
    env = kwargs["env"]
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == str(mock_plan.config)
    assert env["GIT_CONFIG_SYSTEM"] == str(mock_plan.config)
    assert env["HOME"] == str(mock_plan.home) and env["GIT_NO_LAZY_FETCH"] == "1"
    assert "GIT_CONFIG_COUNT" not in env and "GIT_SSH_COMMAND" not in env
    assert "API_KEY" not in env and "PATH" not in env
    assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
    assert kwargs["limit"] == 4096


@pytest.mark.parametrize("failure", ["timeout", "output", "exception", "cancel", "cleanup"])
async def test_interruptions_are_unconfirmed_and_never_retried(
    monkeypatch, s1, mock_plan, no_real_effects, failure,
):
    process = fake_process()
    no_real_effects.spawn.side_effect = None
    no_real_effects.spawn.return_value = process
    errors = {"timeout": TimeoutError, "output": OutputLimitExceeded,
              "exception": OSError, "cancel": asyncio.CancelledError, "cleanup": TimeoutError}
    capture = AsyncMock(side_effect=errors[failure]("private raw detail"))
    cleanup = AsyncMock(side_effect=OSError("cleanup detail") if failure == "cleanup" else None)
    monkeypatch.setattr(s1, "capture_bounded", capture)
    monkeypatch.setattr(s1, "terminate", cleanup)
    expected = asyncio.CancelledError if failure == "cancel" else ToolExecutionUnconfirmed
    with pytest.raises(expected) as caught:
        await s1._execute(mock_plan, "status")
    no_real_effects.spawn.assert_awaited_once()
    capture.assert_awaited_once_with(process, s1.MAX_OUTPUT_BYTES)
    cleanup.assert_awaited_once_with(process)
    assert "private raw detail" not in str(caught.value)
    if failure == "cancel":
        assert "UNCONFIRMED" in " ".join(caught.value.__notes__)


@pytest.mark.parametrize("failure", [OSError, TimeoutError, asyncio.CancelledError])
async def test_spawn_ambiguity_is_not_reported_not_started(
    s1, mock_plan, no_real_effects, failure,
):
    no_real_effects.spawn.side_effect = failure("private startup detail")
    expected = (asyncio.CancelledError if failure is asyncio.CancelledError
                else ToolExecutionUnconfirmed)
    with pytest.raises(expected):
        await s1._execute(mock_plan, "diff")
    no_real_effects.spawn.assert_awaited_once()


async def test_real_bounded_capture_detects_combined_budget(s1, mock_plan, no_real_effects):
    no_real_effects.spawn.side_effect = None
    no_real_effects.spawn.return_value = fake_process(stdout=b"x" * (s1.MAX_OUTPUT_BYTES + 1))
    # Cleanup is mocked: never call Windows taskkill or OS Process.kill.
    original = s1.terminate
    try:
        s1.terminate = AsyncMock()
        with pytest.raises(ToolExecutionUnconfirmed):
            await s1._execute(mock_plan, "diff")
        s1.terminate.assert_awaited_once()
    finally:
        s1.terminate = original


@pytest.mark.parametrize("code", [1, None])
async def test_nonzero_or_unknown_exit_never_returns_success(
    monkeypatch, s1, mock_plan, no_real_effects, code,
):
    no_real_effects.spawn.side_effect = None
    no_real_effects.spawn.return_value = fake_process(code=code)
    monkeypatch.setattr(s1, "terminate", AsyncMock())
    with pytest.raises(ToolExecutionUnconfirmed):
        await s1._execute(mock_plan, "status")


@pytest.mark.parametrize("phase", ["spawn", "capture"])
async def test_actual_deadline_with_mock_child(
    monkeypatch, s1, mock_plan, no_real_effects, phase,
):
    async def never_finishes(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(s1, "TIMEOUT_SECONDS", 0.01)
    cleanup = AsyncMock()
    monkeypatch.setattr(s1, "terminate", cleanup)
    if phase == "spawn":
        no_real_effects.spawn.side_effect = never_finishes
    else:
        no_real_effects.spawn.side_effect = None
        no_real_effects.spawn.return_value = fake_process()
        monkeypatch.setattr(s1, "capture_bounded", never_finishes)
    with pytest.raises(ToolExecutionUnconfirmed):
        await s1._execute(mock_plan, "diff")
    no_real_effects.spawn.assert_awaited_once()
    assert cleanup.await_count == (1 if phase == "capture" else 0)


@pytest.mark.parametrize("operation", ["status", "diff"])
async def test_tool_parameters_cannot_issue_profile(tmp_path, no_real_effects, operation):
    result = await GitInspectTool(git_broker=GitBroker(PolicyEngine())).invoke({
        "operation": operation, "repo": str(tmp_path), "trusted_local": True,
        "profile": {"installation": "C:/arbitrary/git.exe", "managed": True},
        "_sprout_context": {"approval_granted": True, "trusted": True},
    })
    assert not result.ok and "s1_trusted_installation_unavailable" in result.to_text()
    no_real_effects.spawn.assert_not_called()
    no_real_effects.legacy.assert_not_called()


@pytest.mark.parametrize("operation", ["status", "diff"])
async def test_scope_rejection_is_preserved(tmp_path, no_real_effects, operation):
    workspace = Workspace(id="test", root=tmp_path)
    scope = DelegationScope(allowed_actions=("file.read",))
    result = await getattr(GitBroker(PolicyEngine()), operation)(workspace, scope=scope)
    assert not result.allowed
    no_real_effects.spawn.assert_not_called()
    no_real_effects.legacy.assert_not_called()


async def test_cleanup_component_kills_and_reaps_mock_child(
    monkeypatch, s1, mock_plan, no_real_effects,
):
    process = fake_process(code=None)
    no_real_effects.spawn.side_effect = None
    no_real_effects.spawn.return_value = process
    kills = []

    def fake_kill(child):
        kills.append(child)
        child.returncode = -9

    monkeypatch.setattr("Sprout.execution.proc.kill_tree", fake_kill)
    monkeypatch.setattr(s1, "capture_bounded", AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(ToolExecutionUnconfirmed):
        await s1._execute(mock_plan, "diff")
    assert kills == [process]
    process.wait.assert_awaited_once()
