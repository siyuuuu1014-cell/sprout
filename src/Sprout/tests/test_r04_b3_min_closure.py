"""Proposed B3-min contracts: intentionally red until separately approved repair.

Every final Git/process effect is mocked. These tests do not authorize or
execute real Git, and do not import any untracked historical investigation.
"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.apply import ApplyBroker
from Sprout.execution.git_broker import GitBroker
from Sprout.execution.models import ChangeProposal, DiffResult, SandboxRef
from Sprout.runtime.changes import ChangeProposalService
from Sprout.sandbox.git_worktree import GitWorktreeSandbox
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.task.models import Task
from Sprout.tools import system_tools
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry
from Sprout.tools.system_tools import GitWriteTool
from Sprout.workspace.models import Workspace, WorkspaceKind

WRITES = sorted(GitWriteTool._OPERATIONS)
DIRECT = ["log", "branch", "add", "commit", "push", "pull"]


class FixedPolicy:
    def __init__(self, decision=AccessDecision.ALLOW):
        self.decision = decision

    def decide(self, request):
        return PolicyDecision(self.decision, "mock policy for boundary probe")


@pytest.fixture
def effects(monkeypatch):
    git = AsyncMock(return_value=(0, "mock Git success", ""))
    apply = AsyncMock(return_value=(0, "a" * 40 + "\n", ""))

    async def sandbox(*args, **kwargs):
        return 0, " M note.txt\0" if "status" in args else "mock diff", ""

    sandbox = AsyncMock(side_effect=sandbox)
    host = AsyncMock(side_effect=AssertionError("Host fallback forbidden"))
    command = AsyncMock(side_effect=AssertionError("Host command forbidden"))
    spawn = AsyncMock(side_effect=AssertionError("Real subprocess forbidden"))
    monkeypatch.setattr("Sprout.execution.git_broker.run_capture", git)
    monkeypatch.setattr("Sprout.execution.apply.run_capture", apply)
    monkeypatch.setattr("Sprout.sandbox.git_worktree.run_capture", sandbox)
    monkeypatch.setattr(system_tools, "_run_git", host)
    monkeypatch.setattr(system_tools, "_run_command", command)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    yield SimpleNamespace(git=git, apply=apply, sandbox=sandbox, host=host)
    host.assert_not_awaited()
    command.assert_not_awaited()
    spawn.assert_not_awaited()


def workspace(tmp_path):
    return Workspace(id="workspace", root=tmp_path, kind=WorkspaceKind.GIT_REPOSITORY)


def arguments(tmp_path, operation):
    return {"operation": operation, "repo": str(tmp_path), "files": ["note.txt"],
            "message": "mock change", "ref": "HEAD", "remote": "origin", "branch": "topic",
            "trusted_local": True, "profile": "claimed",
            "_sprout_context": {"approval_granted": True, "source": "interactive"}}


async def invoke_direct(broker, operation, ws):
    if operation == "commit":
        return await broker.commit(ws, "mock change", files=("note.txt",), task_id="task")
    if operation in {"push", "pull"}:
        return await getattr(broker, operation)(ws, "origin", "topic", task_id="task")
    if operation == "add":
        return await broker.add(ws, ("note.txt",))
    return await getattr(broker, operation)(ws)


@pytest.mark.parametrize("operation", WRITES)
async def test_all_public_writes_refuse_even_with_allowing_broker(tmp_path, effects, operation):
    result = await GitWriteTool(git_broker=GitBroker(FixedPolicy())).invoke(
        arguments(tmp_path, operation))
    assert not result.ok, "Retired generic write must refuse even under custom Policy ALLOW"
    effects.git.assert_not_awaited()


@pytest.mark.parametrize("operation", DIRECT)
async def test_direct_broker_api_has_no_generic_execution_contract(tmp_path, effects, operation):
    result = await invoke_direct(GitBroker(FixedPolicy()), operation, workspace(tmp_path))
    assert not result.allowed, "Direct Python API must not bypass capability retirement"
    effects.git.assert_not_awaited()


@pytest.mark.parametrize("operation", ["commit", "push", "pull"])
@pytest.mark.parametrize("single_use", [False, True])
async def test_old_real_approval_cannot_reopen_retired_broker(
    tmp_path, effects, operation, single_use,
):
    manager = ApprovalManager(MemoryOperationalStore())
    supplied = ({"message": "mock change", "files": ["note.txt"]} if operation == "commit"
                else {"remote": "origin", "branch": "topic"})
    record = await manager.request("git_write", supplied, task_id="task", single_use=single_use)
    await manager.decide(record.id, True, decided_by="human", channel="cli")
    approved = await manager._store.get_approval(record.id)
    # Real default matrix for commit/push; REQUIRE_APPROVAL test policy for pull.
    policy = FixedPolicy(AccessDecision.REQUIRE_APPROVAL) if operation == "pull" else PolicyEngine()
    broker = GitBroker(policy, approvals=manager)
    result = await invoke_direct(broker, operation, workspace(tmp_path))
    assert not result.allowed, "Historical approval is not a new execution capability"
    effects.git.assert_not_awaited()
    assert await manager._store.get_approval(record.id) == approved


@pytest.mark.parametrize("operation", WRITES)
async def test_retired_write_is_rejected_before_meaningless_human_prompt(
    tmp_path, effects, operation,
):
    manager = ApprovalManager(MemoryOperationalStore())
    registry = ToolRegistry()
    registry.register(GitWriteTool(git_broker=GitBroker(FixedPolicy())))
    executor = ToolExecutor(registry, approvals=manager)
    supplied = arguments(tmp_path, operation)
    identity = dict(task_id="task", session_id="session", source="cli", requested_by="human")
    first = await executor.execute("git_write", supplied, **identity)
    second = None
    if first.approval_id:
        await manager.decide(first.approval_id, True, decided_by="human", channel="cli")
        second = await executor.execute("git_write", supplied, **identity)
    assert first.approval_id is None, (
        f"Unnecessary approval requested; after approval "
        f"ok={None if second is None else second.ok}; "
        f"mock generic Git calls={effects.git.await_count}"
    )
    assert not first.ok
    effects.git.assert_not_awaited()


@pytest.mark.parametrize("operation", DIRECT)
async def test_explicit_policy_deny_remains_zero_effect(tmp_path, effects, operation):
    result = await invoke_direct(GitBroker(FixedPolicy(AccessDecision.DENY)),
                                 operation, workspace(tmp_path))
    assert not result.allowed
    effects.git.assert_not_awaited()


@pytest.mark.parametrize("error", [RuntimeError, TimeoutError, asyncio.CancelledError])
async def test_closed_entry_does_not_reach_erroring_runner_or_host_retry(tmp_path, effects, error):
    effects.git.side_effect = error("mock runner interruption")
    try:
        result = await GitWriteTool(git_broker=GitBroker(FixedPolicy())).invoke(
            arguments(tmp_path, "add"))
    except (RuntimeError, TimeoutError, asyncio.CancelledError):
        result = None
    effects.git.assert_not_awaited()
    assert result is not None and not result.ok


@pytest.mark.parametrize("operation", ["status", "diff"])
async def test_s1_no_issuer_still_blocks_generic_reads(tmp_path, effects, operation):
    result = await getattr(GitBroker(FixedPolicy()), operation)(workspace(tmp_path))
    assert not result.allowed and result.reason == "s1_trusted_installation_unavailable"
    effects.git.assert_not_awaited()


async def test_internal_apply_retains_separate_approval_commit_and_recorded_rollback(
    tmp_path, effects, monkeypatch,
):
    manager = ApprovalManager(MemoryOperationalStore())
    broker = ApplyBroker(PolicyEngine(), approvals=manager)
    proposal = ChangeProposal(id="proposal", task_id="task", files_changed=("note.txt",),
                              diffs=(DiffResult("note.txt", "mock reviewed patch"),))
    ws = workspace(tmp_path)
    withheld = await broker.evaluate(proposal, ws)
    assert not withheld.applied
    effects.apply.assert_not_awaited()
    record = await manager.request(
        "git.commit", {"proposal_id": proposal.id, "risk": proposal.risk},
        task_id="task", single_use=False)
    await manager.decide(record.id, True, decided_by="human", channel="cli")
    # No patch file is created; all Git mutation and reads terminate at mocks.
    monkeypatch.setattr(broker, "_write_patch", lambda *args: str(tmp_path / "absent-mock.patch"))
    applied = await broker.apply(proposal, ws)
    assert applied.applied
    calls = [call.args for call in effects.apply.await_args_list]
    assert any("commit" in call for call in calls)
    assert any("add" in call and "--" in call and "note.txt" in call for call in calls)
    rolled = await broker.rollback(replace(proposal, metadata={"applied_commit": "b" * 40}), ws)
    assert rolled.applied
    assert "revert" in effects.apply.call_args.args
    assert "b" * 40 in effects.apply.call_args.args
    effects.git.assert_not_awaited()


async def test_proposal_service_keeps_gate_and_approval_independent(tmp_path, effects):
    manager = ApprovalManager(MemoryOperationalStore())
    item = ChangeProposal(id="proposal", task_id="task")
    records = {item.id: item}

    async def save(value):
        records[value.id] = value

    metadata = SimpleNamespace(
        get_change_proposal=AsyncMock(side_effect=lambda key: records.get(key)),
        save_change_proposal=AsyncMock(side_effect=save),
        get_task=AsyncMock(return_value=Task(id="task", workspace_id="workspace")),
    )
    apply = SimpleNamespace(apply=AsyncMock())
    service = ChangeProposalService(metadata, manager._store, apply, approvals=manager)
    approved = await service.approve(item.id, decided_by="human")
    assert approved.status.value == "approved"
    result = await service.apply(item.id)
    assert not result.applied  # No acceptance evidence: approval alone must not land it.
    apply.apply.assert_not_awaited()
    effects.git.assert_not_awaited()


async def test_internal_sandbox_diff_and_changed_files_do_not_use_generic_broker(tmp_path, effects):
    provider = GitWorktreeSandbox(workspace(tmp_path))
    ref = SandboxRef("sandbox", "git_worktree", tmp_path)
    diff = await provider.diff(ref)
    paths = await provider.changed_files(ref)
    assert diff.diff_text == "mock diff" and paths == ("note.txt",)
    assert any("add" in call.args and "-N" in call.args for call in effects.sandbox.await_args_list)
    effects.git.assert_not_awaited()


@pytest.mark.parametrize("operation", WRITES)
@pytest.mark.parametrize("mode", ["allow", "approval", "risk_deny", "layer_deny"])
async def test_effective_tool_denial_audit_no_prompt_no_consumption(
    tmp_path, effects, monkeypatch, operation, mode,
):
    from unittest.mock import Mock

    from Sprout.events import TOOL_DENIED, EventBus
    from Sprout.security.policy import SecurityPolicy

    manager = ApprovalManager(MemoryOperationalStore())
    supplied = arguments(tmp_path, operation)
    identity = dict(task_id="task", session_id="session", source="cli", requested_by="human")
    old = await manager.request("git_write", supplied, **identity)
    await manager.decide(old.id, True, decided_by="human", channel="cli")
    approved = await manager._store.get_approval(old.id)
    lookup, request = AsyncMock(wraps=manager.is_approved), AsyncMock(wraps=manager.request)
    monkeypatch.setattr(manager, "is_approved", lookup)
    monkeypatch.setattr(manager, "request", request)
    tool = GitWriteTool(git_broker=GitBroker(FixedPolicy()))
    invoke = AsyncMock(side_effect=AssertionError("Must deny before invoke"))
    monkeypatch.setattr(tool, "invoke", invoke)
    registry = ToolRegistry()
    registry.register(tool)
    policy = SecurityPolicy(
        always_allow=frozenset({"git_write"}) if mode in {"allow", "layer_deny"} else frozenset(),
        always_deny=frozenset({"git_write"}) if mode == "risk_deny" else frozenset(),
    )
    bus, seen = EventBus(), []
    bus.subscribe("*", seen.append)
    audit = SimpleNamespace(record=Mock())
    executor = ToolExecutor(
        registry, approvals=manager, policy=policy, events=bus, audit=audit,
        policy_engine=FixedPolicy(AccessDecision.DENY if mode == "layer_deny"
                                  else AccessDecision.ALLOW),
    )
    result = await executor.execute("git_write", supplied, **identity)
    assert not result.ok and result.approval_id is None
    lookup.assert_not_called()
    request.assert_not_called()
    invoke.assert_not_called()
    assert await manager.pending() == []
    assert await manager._store.get_approval(old.id) == approved
    assert len(seen) == 1 and seen[0].name == TOOL_DENIED
    expected = "policy" if mode.endswith("deny") else "capability_retired"
    assert seen[0].payload == {"tool": "git_write", "reason": expected}
    audit.record.assert_called_once()
    entry = audit.record.call_args.args[1]
    assert entry["decision"] == "deny" and entry["task_id"] == "task"
    assert ("retired" in entry["reason"]) == (expected == "capability_retired")
    effects.git.assert_not_awaited()


@pytest.mark.parametrize("operation", DIRECT)
@pytest.mark.parametrize("decision", list((AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL,
                                         AccessDecision.DENY)))
async def test_broker_preserves_requests_and_emits_only_final_denial(
    tmp_path, effects, monkeypatch, operation, decision,
):
    from Sprout.events import POLICY_DECIDED, EventBus

    requests = []

    class Observe(FixedPolicy):
        def decide(self, request):
            requests.append(request)
            return super().decide(request)

    bus, seen = EventBus(), []
    bus.subscribe("*", seen.append)
    manager = ApprovalManager(MemoryOperationalStore())
    lookup = AsyncMock(side_effect=AssertionError("Must not consume old approval"))
    monkeypatch.setattr(manager, "is_approved", lookup)
    result = await invoke_direct(GitBroker(Observe(decision), approvals=manager, events=bus),
                                 operation, workspace(tmp_path))
    assert not result.allowed
    lookup.assert_not_called()
    assert len(requests) == 1
    assert requests[0].action.value == "git." + operation
    assert requests[0].resource.workspace_id == "workspace"
    assert requests[0].resource.path == str(tmp_path)
    assert requests[0].task_id == ("task" if operation in {"commit", "push", "pull"} else "")
    assert len(seen) == 1 and seen[0].name == POLICY_DECIDED
    payload = seen[0].payload
    assert payload["decision"] == "deny"
    if decision is AccessDecision.DENY:
        assert payload["reason"] == "mock policy for boundary probe"
        assert "capability:git:retired" not in payload["matched_rules"]
    else:
        assert "retired" in payload["reason"]
        assert "capability:git:retired" in payload["matched_rules"]
    effects.git.assert_not_awaited()


@pytest.mark.parametrize("kind", ["same_name_plugin", "subclass"])
@pytest.mark.parametrize("approved_path", [False, True])
async def test_same_name_plugin_and_custom_subclass_keep_original_policy(
    effects, kind, approved_path,
):
    from Sprout.security.policy import SecurityPolicy
    from Sprout.tools.result import ToolResult

    calls = []

    class Plugin:
        spec = GitWriteTool.spec

        async def invoke(self, arguments):
            calls.append(arguments)
            return ToolResult.success("mock custom implementation")

    class CustomSubclass(GitWriteTool):
        invoke = Plugin.invoke

    tool = Plugin() if kind == "same_name_plugin" else CustomSubclass()
    registry = ToolRegistry()
    registry.register(tool)
    manager = ApprovalManager(MemoryOperationalStore())
    executor = ToolExecutor(
        registry, approvals=manager, policy_engine=FixedPolicy(),
        policy=SecurityPolicy(always_allow=frozenset() if approved_path
                              else frozenset({"git_write"})),
    )
    identity = dict(task_id="plugin-task", session_id="plugin-session",
                    requested_by="human", source="cli")
    first = await executor.execute("git_write", {"operation": "plugin-owned"}, **identity)
    if approved_path:
        assert first.approval_id and not first.ok and not calls
        await manager.decide(first.approval_id, True, decided_by="human", channel="cli")
        result = await executor.execute("git_write", {"operation": "plugin-owned"}, **identity)
    else:
        result = first
    assert result.ok and len(calls) == 1
    effects.git.assert_not_awaited()


async def test_private_legacy_runner_is_a_tombstone(tmp_path, effects):
    with pytest.raises(RuntimeError, match="retired"):
        await GitBroker._run(workspace(tmp_path), "push", "origin", "topic")
    effects.git.assert_not_called()


@pytest.mark.parametrize("operation", [None, "unknown", "status"])
async def test_invalid_write_operation_still_validates_input(effects, operation):
    result = await GitWriteTool(git_broker=object()).invoke({"operation": operation})
    assert not result.ok and "Invalid operation" in result.to_text()
    effects.git.assert_not_called()


async def test_expired_scope_remains_denied_and_audited(tmp_path, effects):
    from datetime import UTC, datetime, timedelta

    from Sprout.events import AUTH_SCOPE_EXPIRED, POLICY_DECIDED, EventBus
    from Sprout.task.models import DelegationScope

    bus, seen = EventBus(), []
    bus.subscribe("*", seen.append)
    result = await GitBroker(PolicyEngine(), events=bus).commit(
        workspace(tmp_path), "test", task_id="task",
        scope=DelegationScope(expires_at=datetime.now(UTC) - timedelta(seconds=1)),
    )
    assert not result.allowed
    assert [item.name for item in seen] == [POLICY_DECIDED, AUTH_SCOPE_EXPIRED]
    assert "scope:expired" in seen[0].payload["matched_rules"]
    effects.git.assert_not_called()
