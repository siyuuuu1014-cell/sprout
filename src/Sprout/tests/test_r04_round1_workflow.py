"""Round-one user workflow regressions; no external model or Docker effects."""
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from rich.cells import cell_len

from Sprout.cli.commands import chat
from Sprout.gateway.rpc_gateway import RPCGateway
from Sprout.sandbox.git_worktree import GitWorktreeSandbox
from Sprout.security.approval import ApprovalManager
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.tools.system_tools import CliDownloadTool
from Sprout.workspace.models import Workspace


@pytest.mark.parametrize("columns", [80, 120])
def test_approval_panel_fits_terminal(columns, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "get_terminal_size",
                        lambda *a, **k: shutil.os.terminal_size((columns, 30)))
    record = SimpleNamespace(id="id", tool="cli_tool_run", resource_scope="/a/" * 150,
                             task_id="t", source="cli", requested_by="用户", expires_at=None,
                             action_summary="python " + "很长的命令" * 100, approval_class="")
    chat._print_approval_record(record)
    lines = capsys.readouterr().out.splitlines()
    assert all(cell_len(line) <= columns for line in lines)
    borders = [line for line in lines if line.startswith("+")]
    assert len(borders) == 2 and borders[0] == borders[1]


@pytest.mark.asyncio
async def test_session_reuse_exact_operation_across_events_and_tasks():
    from Sprout.events import EventBus

    events = []
    bus = EventBus()

    async def observe(event):
        events.append(event)

    bus.subscribe("approval.reused", observe)
    manager = ApprovalManager(MemoryOperationalStore(), events=bus)
    args = {"command": "python", "args": ["-m", "pytest"], "cwd": "/fixture", "event_id": "e1"}
    record = await manager.request("cli_tool_run", args, task_id="t1", requested_by="alice",
                                   source="cli", session_id="s", resource_scope="workspace")
    record.single_use = False
    await manager.store.save_approval(record)
    await manager.decide(record.id, True)
    common = dict(task_id="t2", requested_by="alice", source="cli", session_id="s",
                  resource_scope="workspace")
    assert await manager.is_approved("cli_tool_run", {**args, "event_id": "e2"}, **common)
    assert not await manager.is_approved("cli_tool_run", {**args, "args": ["evil.py"]}, **common)
    assert not await manager.is_approved("cli_tool_run", {**args, "cwd": "/other"}, **common)
    assert not await manager.is_approved("cli_tool_run", args, **{**common, "requested_by": "bob"})
    assert not await manager.is_approved("cli_tool_run", args, **{**common, "session_id": "other"})
    assert not await manager.is_approved("cli_tool_run", args, **{**common, "source": "rpc"})
    assert len(events) == 1 and events[0].payload["task_id"] == "t2"
    assert events[0].payload["approval_id"] == record.id


@pytest.mark.asyncio
async def test_download_refuses_before_mkdir_or_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    broker = SimpleNamespace(download=AsyncMock())
    guard = SimpleNamespace(acheck=AsyncMock(return_value=SimpleNamespace(allowed=True)))
    result = await CliDownloadTool(guard=guard, network_broker=broker).invoke(
        {"url": "https://example.invalid/app", "destination": "new/app"})
    assert not result.ok
    assert not (tmp_path / "new").exists()
    broker.download.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["proposal.show", "proposal.approve", "proposal.reject",
                                   "proposal.apply", "proposal.rollback", "task.changes"])
async def test_proposal_rpc_requires_owner_before_any_action(method):
    proposal = SimpleNamespace(id="p", task_id="t")
    runtime = SimpleNamespace(find_change_proposal=AsyncMock(return_value=proposal),
                              get_task=AsyncMock(), approve_change_proposal=AsyncMock(),
                              reject_change_proposal=AsyncMock(), apply_change_proposal=AsyncMock(),
                              rollback_change_proposal=AsyncMock())
    gateway = RPCGateway(runtime)
    with pytest.raises(PermissionError):
        await gateway._dispatch(method, {"proposal_id": "p", "task_id": "t"}, principal=None)
    runtime.approve_change_proposal.assert_not_awaited()
    runtime.apply_change_proposal.assert_not_awaited()


@pytest.mark.asyncio
async def test_host_management_rejects_arbitrary_git(tmp_path, monkeypatch):
    from Sprout.sandbox import git_worktree
    capture = AsyncMock(return_value=(0, "", ""))
    monkeypatch.setattr(git_worktree, "run_capture", capture)
    (tmp_path / ".git").mkdir()
    sandbox = GitWorktreeSandbox(Workspace(id="w", root=tmp_path))
    with pytest.raises((PermissionError, ValueError)):
        await sandbox._run("git", "-C", str(tmp_path), "push", "origin", "main")
    capture.assert_not_awaited()

@pytest.mark.asyncio
async def test_session_grant_persists_and_expiry_and_once_are_enforced(tmp_path):
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from Sprout.security.commands import CommandRegistry
    from Sprout.storage.local.sqlite.driver import SqliteDatabase
    from Sprout.storage.local.sqlite.operational import SqliteOperationalStore

    db = SqliteDatabase(tmp_path / "grants.db")
    try:
        store = SqliteOperationalStore(db)
        manager = ApprovalManager(store, commands=CommandRegistry())
        args = {"command": "sprout", "args": ["info"], "cwd": "/fixture"}
        common = dict(session_id="s", requested_by="alice", source="cli", resource_scope="w")
        grant = await manager.request("cli_tool_run", args, task_id="t1", **common)
        await store.save_approval(replace(grant, single_use=False))
        await manager.decide(grant.id, True)
        later = ApprovalManager(store)
        assert await later.is_approved("cli_tool_run", args, task_id="t2", **common)
        assert not await later.is_approved("cli_tool_run", args, task_id="t2",
                                           **{**common, "resource_scope": "other"})
        assert not await later.is_approved("cli_tool_run", {**args, "args": ["project", "apply"]},
                                           task_id="t2", **common)
        saved = await store.get_approval(grant.id)
        await store.save_approval(replace(saved, expires_at=datetime.now(UTC)-timedelta(seconds=1)))
        assert not await later.is_approved("cli_tool_run", args, task_id="t2", **common)
        once = await later.request("cli_tool_run", args, task_id="t3", **common)
        await later.decide(once.id, True)
        assert await later.is_approved("cli_tool_run", args, task_id="t3", **common)
        assert not await later.is_approved("cli_tool_run", args, task_id="t3", **common)
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["owner", "other", "source", "unauthenticated"])
async def test_proposal_rpc_validates_persisted_task_owner(identity):
    from Sprout.gateway.identity import Principal

    actor = Principal(user_id="alice", source="rpc", authenticated=True)
    principal = Principal(user_id="bob" if identity == "other" else "alice",
                          source="cli" if identity == "source" else "rpc",
                          authenticated=identity != "unauthenticated")
    runtime = SimpleNamespace(find_change_proposal=AsyncMock(return_value=SimpleNamespace(
        id="p", task_id="t")), get_task=AsyncMock(return_value=SimpleNamespace(actor=actor)),
        approve_change_proposal=AsyncMock(return_value=SimpleNamespace(
            id="p", status=SimpleNamespace(
            value="approved"))))
    gateway = RPCGateway(runtime)
    if identity == "owner":
        assert await gateway._dispatch("proposal.approve", {"proposal_id": "p"},
                                       principal=principal) == {"id": "p", "status": "approved"}
        runtime.approve_change_proposal.assert_awaited_once()
    else:
        with pytest.raises(PermissionError):
            await gateway._dispatch("proposal.approve", {"proposal_id": "p"}, principal=principal)
        runtime.approve_change_proposal.assert_not_awaited()


@pytest.mark.asyncio
async def test_network_broker_download_cannot_bypass_file_gate(tmp_path):
    from Sprout.execution.network_broker import NetworkBroker
    from Sprout.security.engine import PolicyEngine

    broker = NetworkBroker(PolicyEngine())
    target = tmp_path / "absent" / "app"
    result = await broker.download(Workspace(id="w", root=tmp_path),
                                   "https://example.invalid/app", target)
    assert not result.allowed and not target.parent.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["你能做什么？", "Sprout有哪些功能？", "What can you do?",
                                      "你支持写代码吗？", "Can you write code?"])
async def test_capability_questions_never_offer_tools_or_classify_with_model(question):
    from Sprout.context.context import AgentContext
    from Sprout.events import EventBus
    from Sprout.message import Message
    from Sprout.runtime.runtime import Runtime
    from Sprout.session.models import Session

    runtime = object.__new__(Runtime)
    runtime.events = EventBus()
    context = AgentContext(session=Session(id="s", user_id="u"),
                           tools={"project_analyze": object()})
    message = Message(content=question, user_id="u", channel="cli")
    classification = await runtime._classify_intent_with_llm(message, context)
    assert classification["intent"] == "conversation"
    message, selected = await runtime._recognize_intent(
        message, context, classification={"intent": "workspace"})
    assert not selected.tools and "resume_tool_calls" not in message.metadata


@pytest.mark.parametrize("question", ["请扫描这个项目", "你能修改src/app.py吗？", "分析仓库统计"])
def test_real_project_requests_are_not_captured_as_capability_questions(question):
    from Sprout.intents import capability_question

    assert not capability_question(question)


@pytest.mark.asyncio
async def test_host_management_fixed_create_has_no_model_git_surface(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from Sprout.execution import host_acceptance
    from Sprout.sandbox import isolated_git_worktree as isolated

    # Contract unit only: native directory handle behavior has separate G1 evidence.
    @contextmanager
    def directory_pin(_):
        yield SimpleNamespace(check=lambda: None)

    monkeypatch.setattr(host_acceptance, "pin_execution_directory", directory_pin)

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    executable = tmp_path / "git.exe"
    executable.write_bytes(b"fixed test executable, never run")
    monkeypatch.setattr(isolated.shutil, "which", lambda _: str(executable))
    capture = AsyncMock(side_effect=[(1, "", ""), (0, "", "")])
    monkeypatch.setattr(isolated, "run_capture", capture)
    sandbox = GitWorktreeSandbox(Workspace(id="w", root=root))
    ref = await sandbox.create(branch="sprout-sandbox-test")
    assert ref.root == root.parent / ".sprout-sprout-sandbox-test"
    assert capture.await_count == 2
    actual = capture.await_args_list[-1]
    assert actual.args[0] == str(executable)
    assert "core.hooksPath=" in " ".join(actual.args)
    assert actual.kwargs["timeout"] == 30
    with pytest.raises(PermissionError):
        await sandbox.diff(ref)

@pytest.mark.asyncio
async def test_permission_inventory_is_live_and_never_executes_tools():
    from Sprout.cli.commands.security import permission_inventory
    from Sprout.security.commands import CommandRegistry
    from Sprout.security.policy import SecurityPolicy
    from Sprout.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(CliDownloadTool())
    layer = SimpleNamespace(tool_policy=SecurityPolicy(), commands=CommandRegistry(
        auto_run=frozenset({"safe-command"}), denylist=frozenset({"bad-command"})))
    runtime = SimpleNamespace(security=layer, tools=registry,
                              _policy_engine=SimpleNamespace(_layers=()))
    result = permission_inventory(runtime)
    assert result["tools"][0]["tool_gate"] == "deny"
    assert result["command_auto_run"] == ["safe-command"]
    assert result["command_denylist"] == ["bad-command"]


@pytest.mark.asyncio
async def test_session_reuse_never_turns_empty_resource_into_a_wildcard():
    manager = ApprovalManager(MemoryOperationalStore())
    args = {"command": "python", "args": ["check.py"], "cwd": "/fixture"}
    common = dict(session_id="s", requested_by="a", source="cli")
    record = await manager.request("cli_tool_run", args, task_id="t1", **common)
    record.single_use = False
    await manager.store.save_approval(record)
    await manager.decide(record.id, True)
    assert not await manager.is_approved("cli_tool_run", args, task_id="t2",
                                        resource_scope="other", **common)

@pytest.mark.asyncio
async def test_unavailable_download_never_requests_or_consumes_an_approval():
    from Sprout.tools.executor import ToolExecutor
    from Sprout.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(CliDownloadTool())
    approvals = ApprovalManager(MemoryOperationalStore())
    executor = ToolExecutor(tools=registry, approvals=approvals)
    result = await executor.execute("cli_tool_download", {"url": "https://example.invalid/app"})
    assert not result.ok
    assert await approvals.pending() == []

@pytest.mark.parametrize("change", ["backlink", "commondir", "foreign"])
def test_host_management_refuses_unpaired_worktrees(tmp_path, change):
    from Sprout.sandbox.isolated_git_worktree import IsolatedGitWorktreeSandbox

    repo = tmp_path / "repo"
    common = repo / ".git"
    work = tmp_path / ".sprout-sprout-sandbox-owned"
    management = common / "worktrees" / "owned"
    management.mkdir(parents=True)
    work.mkdir()
    (work / ".git").write_text("gitdir: " + str(management))
    (management / "gitdir").write_text(str(work / ".git"))
    (management / "commondir").write_text("../..")
    if change == "backlink":
        (management / "gitdir").write_text(str(tmp_path / "foreign/.git"))
    elif change == "commondir":
        (management / "commondir").write_text("../../../foreign")
    else:
        (work / ".git").write_text("gitdir: " + str(tmp_path / "foreign"))
    with pytest.raises((ValueError, PermissionError)):
        IsolatedGitWorktreeSandbox(Workspace(id="w", root=repo))._check_git_root(work)
