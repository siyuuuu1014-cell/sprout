"""Disabled internal Apply capability: no authorization consumption or I/O.

These are Mock-only route tests, not native Git or transport E2E evidence.
"""

import asyncio
import subprocess
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.events import EventBus
from Sprout.execution import acceptance
from Sprout.execution import apply as apply_module
from Sprout.execution.apply import ApplyBroker
from Sprout.execution.models import ChangeProposal, ChangeProposalStatus, DiffResult
from Sprout.execution.models import TestResult as CheckResult
from Sprout.runtime.changes import ChangeProposalService
from Sprout.runtime.nodes import NodeExecutor
from Sprout.runtime.runtime import Runtime
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import DelegationScope, Task
from Sprout.workspace.models import Workspace


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    original_git, original_undo = ApplyBroker._git, ApplyBroker._undo
    original_capture = apply_module.run_capture
    blocked = []
    for target, name, asynchronous in [
        (ApplyBroker, "_git", True), (ApplyBroker, "_undo", True),
        (ApplyBroker, "_write_patch", False), (apply_module, "run_capture", True),
        (apply_module.tempfile, "NamedTemporaryFile", False),
        (asyncio, "create_subprocess_exec", True), (asyncio, "create_subprocess_shell", True),
        (subprocess, "run", False), (subprocess, "Popen", False),
    ]:
        factory = AsyncMock if asynchronous else Mock
        spy = factory(side_effect=AssertionError("Disabled Apply reached an execution effect"))
        monkeypatch.setattr(target, name, spy)
        blocked.append(spy)
    yield SimpleNamespace(
        workspace=Workspace(id="ws", root=tmp_path), blocked=blocked,
        original_git=original_git, original_undo=original_undo,
        original_capture=original_capture,
    )
    for spy in blocked:
        spy.assert_not_called()


def item():
    spec = AcceptanceSpec.from_payload({"cases": [{
        "id": "normal", "target": "example.app:echo", "args": [1], "expected_output": 1,
    }]})
    return ChangeProposal(
        id="proposal", task_id="task", status=ChangeProposalStatus.APPROVED,
        files_changed=("task.txt",), diffs=(DiffResult("task.txt", "mock reviewed patch"),),
        test_results=(CheckResult("acceptance:normal: python", True),
                      CheckResult("poc: demo", True)),
        metadata={"verification": {
            "acceptance_spec": spec.to_payload(), "status": "passed", "demo_path": "demo.py",
            "local_baseline_valid": True, "local_executor": acceptance.local_executor_binding(),
        }},
    )


async def manager_with_record(proposal, mode):
    manager = ApprovalManager(MemoryOperationalStore())
    record = None
    if mode != "none":
        record = await manager.request(
            "git.commit", {"proposal_id": proposal.id, "risk": proposal.risk},
            task_id=proposal.task_id, single_use=mode == "single",
        )
        await manager.decide(record.id, True, decided_by="test-human", channel="cli")
        record = await manager._store.get_approval(record.id)
    return manager, record


def assert_refused(result, operation):
    assert result.applied is False
    assert result.reason == f"internal_{operation}_execution_unavailable"
    assert not result.base_commit and not result.applied_commit


@pytest.mark.parametrize("operation", ["apply", "rollback"])
@pytest.mark.parametrize("mode", ["none", "single", "reusable", "legacy"])
@pytest.mark.parametrize("policy_decision", list((
    AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL, AccessDecision.DENY,
)))
async def test_public_entries_refuse_before_policy_or_approval(
    guarded, monkeypatch, operation, mode, policy_decision,
):
    proposal = item()
    if mode == "legacy":
        proposal = replace(proposal, metadata={})
    manager, record = await manager_with_record(proposal, mode)
    lookup = AsyncMock(side_effect=AssertionError("Must not consume approval"))
    request = AsyncMock(side_effect=AssertionError("Must not request approval"))
    monkeypatch.setattr(manager, "is_approved", lookup)
    monkeypatch.setattr(manager, "request", request)
    policy = PolicyEngine()
    decide = Mock(return_value=PolicyDecision(policy_decision, "test policy"))
    monkeypatch.setattr(policy, "decide", decide)
    bus, events = EventBus(), []
    bus.subscribe("*", events.append)
    broker = ApplyBroker(policy, approvals=manager, events=bus)
    result = await getattr(broker, operation)(proposal, guarded.workspace,
                                            scope=DelegationScope())
    assert_refused(result, operation)
    decide.assert_not_called()
    lookup.assert_not_called()
    request.assert_not_called()
    assert not events  # Capability unavailable is NOT a fabricated policy DENY.
    if record is not None:
        assert await manager._store.get_approval(record.id) == record
    assert await manager.pending() == []


@pytest.mark.parametrize("shape", ["empty_patch", "empty_files", "claimed_authority",
                                   "staged_index"])
async def test_proposal_shape_or_claims_cannot_bypass_shutdown(guarded, monkeypatch, shape):
    proposal = item()
    if shape == "empty_patch":
        proposal = replace(proposal, diffs=())
    elif shape == "empty_files":
        proposal = replace(proposal, files_changed=())
    elif shape == "claimed_authority":
        proposal = replace(proposal, metadata={
            "validated": True, "trusted_local": True, "approval_granted": True,
        })
        monkeypatch.setenv("SPROUT_APPLY_ENABLED", "1")
    # staged_index is deliberately unread: even trying an index probe fails the guard.
    broker = ApplyBroker(PolicyEngine())
    broker.evaluate = AsyncMock(side_effect=AssertionError("Admission must not be reached"))
    result = await broker.apply(proposal, guarded.workspace)
    assert_refused(result, "apply")
    broker.evaluate.assert_not_called()


@pytest.mark.parametrize("history", ["full_oid", "short_oid", "legacy_patch", "moved_head"])
async def test_all_rollback_histories_are_unavailable(guarded, history):
    proposal = item()
    metadata = {} if history == "legacy_patch" else {
        "applied_commit": "abc" if history == "short_oid" else "b" * 40,
        "base_commit": "a" * 40,
    }
    proposal = replace(proposal, status=ChangeProposalStatus.APPLIED, metadata=metadata)
    result = await ApplyBroker(PolicyEngine()).rollback(proposal, guarded.workspace)
    assert_refused(result, "rollback")


@pytest.mark.parametrize("leaf", ["git", "undo", "legacy_capture", "head"])
async def test_real_private_leaves_have_no_host_execution(guarded, leaf):
    broker = ApplyBroker(PolicyEngine())
    with pytest.raises(RuntimeError, match="execution_unavailable"):
        if leaf == "git":
            await guarded.original_git(broker, guarded.workspace, "commit", "-m", "test")
        elif leaf == "undo":
            await guarded.original_undo(broker, guarded.workspace, "never.patch", ("task.txt",))
        elif leaf == "legacy_capture":
            await guarded.original_capture("git", "apply", "-R", "never.patch")
        else:
            # _head_commit delegates to the now closed _git; inspect the real leaf
            # without replacing it with the fixture's zero-call spy.
            broker._git = guarded.original_git.__get__(broker)
            await broker._head_commit(guarded.workspace)


@pytest.mark.parametrize("route", ["service", "force", "rollback", "node_service",
                                   "node_direct", "runtime", "runtime_rollback"])
async def test_upper_routes_do_not_persist_delivery_or_fallback(guarded, route):
    proposal = item()
    if route in {"rollback", "runtime_rollback"}:
        proposal = replace(proposal, status=ChangeProposalStatus.APPLIED,
                           metadata={"applied_commit": "b" * 40})
    if route == "force":
        proposal = replace(proposal, metadata={})  # Preserve genuine unknown-source override.
    manager, old = await manager_with_record(proposal, "single")
    records, seen = {proposal.id: proposal}, []

    async def save(value):
        records[value.id] = value

    task = Task(id="task", workspace_id="ws")
    metadata = SimpleNamespace(
        get_change_proposal=AsyncMock(side_effect=lambda key: records.get(key)),
        save_change_proposal=AsyncMock(side_effect=save), get_task=AsyncMock(return_value=task),
        get_workspace=AsyncMock(return_value=guarded.workspace),
        list_change_proposals=AsyncMock(return_value=[proposal]),
    )
    bus = EventBus()
    bus.subscribe("*", seen.append)
    broker = ApplyBroker(PolicyEngine(), approvals=manager, events=bus)
    service = ChangeProposalService(metadata, manager._store, broker, approvals=manager, events=bus)
    if route.startswith("node_"):
        node = object.__new__(NodeExecutor)
        node._metadata, node._local_acceptance = metadata, True
        node._isolated_coding_required = False
        node._changes = service if route == "node_service" else None
        node._apply_broker = broker
        with pytest.raises(RuntimeError, match="internal_apply_execution_unavailable"):
            await node._apply(guarded.workspace, task)
    elif route.startswith("runtime"):
        runtime = object.__new__(Runtime)
        runtime._changes = lambda: service
        cleanup = AsyncMock(side_effect=AssertionError("No success cleanup"))
        runtime._sandbox_lifecycle = SimpleNamespace(cleanup_proposal=cleanup)
        method = (runtime.rollback_change_proposal if route == "runtime_rollback"
                  else runtime.apply_change_proposal)
        result = await method(proposal.id)
        assert_refused(result, "rollback" if route == "runtime_rollback" else "apply")
        cleanup.assert_not_called()
    else:
        if route == "force":
            result = await service.apply(proposal.id, force=True, override_reason="explicit waiver")
        elif route == "rollback":
            result = await service.rollback(proposal.id)
        else:
            result = await service.apply(proposal.id)
        assert_refused(result, "rollback" if route == "rollback" else "apply")
    assert records[proposal.id].status is proposal.status
    assert await manager._store.get_approval(old.id) == old
    if route == "force":
        override = records[proposal.id].metadata["verification_override"]
        assert override["reason"] == "explicit waiver" and override["verified"] is False
        assert "unknown" in override["blocker"]
        assert len(seen) == 1 and seen[0].name == "verification.overridden"
        assert seen[0].payload["verified"] is False
    else:
        metadata.save_change_proposal.assert_not_called()
        assert not seen
