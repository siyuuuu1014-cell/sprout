"""R04-B design probes: no real Git, hooks, network or file effects.

Desired safety assertions intentionally remain red until separately approved
production repairs. Fake final I/O proves dispatch, not actual Git exploitation.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.git_broker import GitBroker
from Sprout.execution.git_env import git_env
from Sprout.execution.models import GitResult
from Sprout.security.access import AccessDecision, ActionRequest, ActionType, PolicyDecision
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.task.models import DelegationScope
from Sprout.tools import system_tools
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry
from Sprout.tools.result import ToolExecutionUnconfirmed, ToolResult
from Sprout.tools.system_tools import GitInspectTool, GitWriteTool
from Sprout.workspace.models import ResourceKind, ResourceRef, Workspace, WorkspaceKind


@pytest.fixture
def effects(monkeypatch):
    fallback = AsyncMock(return_value=ToolResult.success("mock Git only"))
    capture = AsyncMock(return_value=(0, "mock Git only", ""))
    spawn = AsyncMock(side_effect=AssertionError("Real process forbidden"))
    monkeypatch.setattr(system_tools, "_run_git", fallback)
    monkeypatch.setattr("Sprout.execution.git_broker.run_capture", capture)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return SimpleNamespace(fallback=fallback, capture=capture, spawn=spawn)


class AllowPolicy:
    def decide(self, request):
        return PolicyDecision(AccessDecision.ALLOW, "test-only allow for boundary probes")


def workspace(tmp_path):
    return Workspace(id="test-repo", root=tmp_path, kind=WorkspaceKind.GIT_REPOSITORY)


def invocation(tmp_path, operation):
    return dict(operation=operation, repo=str(tmp_path), files=["note.txt"],
                message="test", branch="topic", ref="HEAD", remote="origin",
                upstream="topic", tag="v-test", stash="stash@{0}",
                url="https://example.invalid/repo", directory=str(tmp_path / "clone"))


CASES = [(kind, operation) for kind, tool in
         (("inspect", GitInspectTool), ("write", GitWriteTool))
         for operation in sorted(tool._OPERATIONS)]


@pytest.mark.parametrize("kind,operation", CASES)
async def test_every_operation_without_broker_must_refuse(tmp_path, effects, kind, operation):
    tool = GitInspectTool() if kind == "inspect" else GitWriteTool()
    result = await tool.invoke(invocation(tmp_path, operation))
    assert not result.ok, "Missing broker must not execute a host Git fallback"
    effects.fallback.assert_not_awaited()
    effects.capture.assert_not_awaited()
    effects.spawn.assert_not_awaited()


@pytest.mark.parametrize("operation,expected", [
    ("log", {"ref": "HEAD"}),
    ("diff", {"files": ["note.txt"], "base": "main", "ref": "HEAD", "name_only": True}),
])
async def test_delegated_inspect_does_not_drop_requested_filters(
    tmp_path, effects, operation, expected,
):
    method = AsyncMock(return_value=GitResult(action=operation, allowed=False, reason="denied"))
    tool = GitInspectTool(git_broker=SimpleNamespace(**{operation: method}))
    await tool.invoke({**invocation(tmp_path, operation), **expected})
    method.assert_awaited_once()
    for key, value in expected.items():
        assert method.call_args.kwargs.get(key) == value
    effects.fallback.assert_not_awaited()


async def test_delegated_pull_keeps_remote_and_upstream(tmp_path, effects):
    method = AsyncMock(return_value=GitResult(action="pull", allowed=False, reason="denied"))
    tool = GitWriteTool(git_broker=SimpleNamespace(pull=method))
    await tool.invoke({**invocation(tmp_path, "pull"), "remote": "reviewed-remote"})
    method.assert_awaited_once()
    assert method.call_args.kwargs.get("remote") == "reviewed-remote"
    assert method.call_args.kwargs.get("branch") == "topic"
    effects.fallback.assert_not_awaited()


async def test_genuine_outer_commit_approval_has_one_prompt_and_bound_identity(tmp_path, effects):
    manager = ApprovalManager(MemoryOperationalStore())
    requests = []
    base = PolicyEngine()

    class Observe:
        def decide(self, request):
            requests.append(request)
            return base.decide(request)

    tool = GitWriteTool(git_broker=GitBroker(Observe(), approvals=manager))
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry, approvals=manager)
    supplied = invocation(tmp_path, "commit")
    identity = dict(task_id="task", session_id="session", requested_by="owner", source="cli")
    first = await executor.execute("git_write", supplied, **identity)
    assert first.approval_id and not first.ok
    await manager.decide(first.approval_id, True, decided_by="owner", channel="cli")
    result = await executor.execute("git_write", supplied, **identity)
    assert result.ok, "Approved outer arguments must not lose their binding inside GitBroker"
    assert requests[-1].task_id == "task"
    assert requests[-1].actor.user_id == "owner"
    assert effects.capture.await_count == 2
    effects.fallback.assert_not_awaited()


async def test_commit_cannot_stage_scope_denied_file_even_with_exact_grant(tmp_path, effects):
    manager = ApprovalManager(MemoryOperationalStore())
    files = ("private/note.txt",)
    record = await manager.request("git_write", {"message": "test", "files": list(files)},
                                   task_id="task")
    await manager.decide(record.id, True, decided_by="owner", channel="cli")
    scope = DelegationScope(allowed_paths=(str(tmp_path),),
                            denied_paths=(str(tmp_path / "private"),))
    result = await GitBroker(PolicyEngine(), approvals=manager).commit(
        workspace(tmp_path), "test", files=files, task_id="task", scope=scope,
    )
    assert not result.allowed
    effects.capture.assert_not_awaited()


@pytest.mark.parametrize("source", ["cron", "automation"])
async def test_unattended_origin_grant_cannot_be_used_as_interactive_default(
    tmp_path, effects, source,
):
    manager = ApprovalManager(MemoryOperationalStore())
    record = await manager.request("git_write", {"message": "test", "files": ["note.txt"]},
                                   task_id="task", requested_by="owner", source=source)
    await manager.decide(record.id, True, decided_by="owner", channel="cli")
    result = await GitBroker(PolicyEngine(), approvals=manager).commit(
        workspace(tmp_path), "test", files=("note.txt",), task_id="task",
    )
    assert not result.allowed, "A missing broker source is not trusted interactive attribution"
    effects.capture.assert_not_awaited()


async def test_broker_file_operands_are_not_options(tmp_path, effects):
    result = await GitBroker(AllowPolicy()).add(workspace(tmp_path), files=("--all",))
    assert not result.allowed
    effects.capture.assert_not_awaited()


@pytest.mark.parametrize("remote,branch", [
    ("origin", "+HEAD:refs/heads/main"),
    ("origin", ":refs/heads/topic"),
    ("origin", "refs/heads/*:refs/heads/*"),
    ("ext::unreviewed-helper", "topic"),
])
async def test_push_rejects_force_delete_wildcard_or_helper_operands(
    tmp_path, effects, remote, branch,
):
    result = await GitBroker(AllowPolicy()).push(workspace(tmp_path), remote, branch)
    assert not result.allowed
    effects.capture.assert_not_awaited()


async def test_broker_diff_explicitly_disables_textconv(tmp_path, effects):
    await GitBroker(AllowPolicy()).diff(workspace(tmp_path))
    assert "--no-ext-diff" in effects.capture.call_args.args
    assert "--no-textconv" in effects.capture.call_args.args


async def test_broker_execution_has_a_finite_timeout(tmp_path, effects):
    await GitBroker(AllowPolicy()).status(workspace(tmp_path))
    timeout = effects.capture.call_args.kwargs.get("timeout")
    assert isinstance(timeout, int | float) and 0 < timeout <= 120


async def test_mutating_timeout_is_unknown_and_never_retried(tmp_path, effects):
    effects.capture.side_effect = TimeoutError("mock timeout after possible effect")
    with pytest.raises(ToolExecutionUnconfirmed):
        await GitBroker(AllowPolicy()).commit(workspace(tmp_path), "test", files=("note.txt",))
    effects.capture.assert_awaited_once()
    effects.fallback.assert_not_awaited()


def test_git_environment_explicitly_blocks_unreviewed_user_config(tmp_path):
    environment = git_env({"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)})
    assert environment.get("GIT_CONFIG_NOSYSTEM") == "1"
    assert environment.get("GIT_CONFIG_GLOBAL")


@pytest.mark.parametrize("source", ["cron", "automation"])
async def test_standard_executor_still_refuses_unattended_write(tmp_path, effects, source):
    registry = ToolRegistry()
    registry.register(GitWriteTool())
    manager = ApprovalManager(MemoryOperationalStore())
    result = await ToolExecutor(registry, approvals=manager).execute(
        "git_write", invocation(tmp_path, "push"), source=source, task_id="task",
    )
    assert not result.ok and not result.approval_id
    assert not await manager.pending()
    effects.fallback.assert_not_awaited()
    effects.capture.assert_not_awaited()
    effects.spawn.assert_not_awaited()


@pytest.mark.parametrize("kind", ["inspect", "write"])
@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError, asyncio.CancelledError])
async def test_delegated_broker_interruption_is_not_host_retry(tmp_path, effects, kind, failure):
    operation = "status" if kind == "inspect" else "add"
    method = AsyncMock(side_effect=failure("mock interruption"))
    broker = SimpleNamespace(**{operation: method})
    tool = (GitInspectTool(git_broker=broker) if kind == "inspect"
            else GitWriteTool(git_broker=broker))
    with pytest.raises(failure):
        await tool.invoke(invocation(tmp_path, operation))
    method.assert_awaited_once()
    effects.fallback.assert_not_awaited()
    effects.capture.assert_not_awaited()


async def test_git_tool_does_not_treat_cli_bool_as_a_grant(tmp_path, effects):
    broker = GitBroker(PolicyEngine(), approvals=ApprovalManager(MemoryOperationalStore()))
    supplied = invocation(tmp_path, "commit")
    supplied["_sprout_context"] = {"approval_granted": True, "source": "cli"}
    assert not (await GitWriteTool(git_broker=broker).invoke(supplied)).ok
    effects.capture.assert_not_awaited()
    effects.fallback.assert_not_awaited()


@pytest.mark.parametrize("action,expected", [
    (ActionType.GIT_STATUS, AccessDecision.ALLOW),
    (ActionType.GIT_DIFF, AccessDecision.ALLOW),
    (ActionType.GIT_LOG, AccessDecision.DENY),
    (ActionType.GIT_BRANCH, AccessDecision.DENY),
    (ActionType.GIT_ADD, AccessDecision.DENY),
    (ActionType.GIT_PULL, AccessDecision.DENY),
    (ActionType.GIT_COMMIT, AccessDecision.REQUIRE_APPROVAL),
    (ActionType.GIT_PUSH, AccessDecision.REQUIRE_APPROVAL),
])
def test_observed_current_base_policy_matrix_is_not_mistaken_for_bypass(tmp_path, action, expected):
    decision = PolicyEngine().decide(ActionRequest(
        action=action, resource=ResourceRef(workspace_id="test", path=str(tmp_path),
                                          kind=ResourceKind.CONFIG),
    ))
    assert decision.decision is expected
