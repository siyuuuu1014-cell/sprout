"""Regression checks for authorization and verified task completion."""

from pathlib import Path

import pytest

from Sprout.execution.file_broker import FileBroker
from Sprout.execution.models import SandboxRef
from Sprout.execution.models import TestResult as VerificationResult
from Sprout.execution.verification import proposal_verification_blocker, verification_blocker
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.security.layered_policy import LayeredPolicyEngine, PolicyLayer, PolicyRule
from Sprout.security.policy import SecurityPolicy
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry
from Sprout.tools.result import ToolResult
from Sprout.tools.spec import ToolSpec


class FixedPolicy:
    def __init__(self, decision: AccessDecision) -> None:
        self.decision = decision

    def decide(self, request) -> PolicyDecision:
        return PolicyDecision(self.decision, reason="test decision")


def test_legacy_green_proposal_cannot_bypass_requirement_evidence():
    from Sprout.execution.models import ChangeProposal

    proposal = ChangeProposal(task_id="legacy", test_results=(
        VerificationResult(name="test: pytest", passed=True),
    ))
    assert "legacy proposal" in proposal_verification_blocker(proposal)


def test_generic_green_checks_cannot_replace_acceptance_and_demo():
    verification = {"status": "passed", "acceptance_required": True}
    generic = VerificationResult(name="test: pytest", passed=True)
    assert "specification is missing" in verification_blocker([generic], verification)
    verification["acceptance_spec"] = {"cases": [
        {"id": "normal", "target": "app:run", "args": [1], "expected_output": 2},
    ]}
    assert "normal has no passing result" in verification_blocker([generic], verification)
    specific = VerificationResult(name="acceptance:normal: python -m pytest", passed=True)
    assert "minimal demo" in verification_blocker([generic, specific], verification)
    verification["demo_path"] = "examples/demo.py"
    demo = VerificationResult(name="poc: python examples/demo.py", passed=True)
    assert verification_blocker([generic, specific, demo], verification) == ""


@pytest.mark.parametrize("decision", list(AccessDecision))
async def test_delete_requires_an_allow_decision(tmp_path: Path, decision) -> None:
    target = tmp_path / "source.py"
    target.write_text("value = 1\n", encoding="utf-8")
    sandbox = SandboxRef(id="s", kind="git_worktree", root=tmp_path)
    result = await FileBroker(FixedPolicy(decision)).delete(sandbox, "source.py")
    allowed = decision is AccessDecision.ALLOW
    assert result.wrote is allowed
    assert target.exists() is not allowed


class RecordingTool:
    spec = ToolSpec(name="execute", description="test", risk_level="low")

    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, arguments) -> ToolResult:
        self.calls += 1
        return ToolResult.success("executed")


async def test_tool_hard_floor_cannot_be_overridden_by_always_allow() -> None:
    tool = RecordingTool()
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(
        tools=registry,
        policy=SecurityPolicy(always_allow=frozenset({"execute"})),
    )
    result = await executor.execute("execute", {"command": "rm", "args": ["-rf", "/"]})
    assert not result.ok
    assert tool.calls == 0


async def test_tool_respects_shared_organization_policy() -> None:
    tool = RecordingTool()
    registry = ToolRegistry()
    registry.register(tool)
    engine = LayeredPolicyEngine(
        organization=PolicyLayer("organization", [PolicyRule(action="tool.invoke")]),
    )
    executor = ToolExecutor(tools=registry, policy_engine=engine)
    result = await executor.execute("execute", {})
    assert not result.ok
    assert tool.calls == 0


async def test_scoped_tool_uses_its_server_side_risk() -> None:
    tool = RecordingTool()
    engine = LayeredPolicyEngine(
        risk=SecurityPolicy(), tool_risk_lookup=lambda _: "critical",
    )
    executor = ToolExecutor(tools=ToolRegistry(), policy_engine=engine)
    result = await executor.with_scoped_tools({"execute": tool}).execute(
        "execute", {"risk_level": "critical"},
    )
    assert result.ok
    assert tool.calls == 1


@pytest.mark.parametrize("results,status,blocked", [
    ([], "passed", True),
    ([VerificationResult("pytest", False, executed=False)], "not_verified", True),
    ([VerificationResult("pytest", True)], "partially_verified", True),
    ([VerificationResult("pytest", False)], "failed", True),
    ([VerificationResult("pytest", True)], "passed", False),
])
def test_verification_needs_passing_executed_evidence(results, status, blocked) -> None:
    assert bool(verification_blocker(results, {"status": status})) is blocked


async def test_forced_apply_records_reason_before_io_without_verifying_task(tmp_path) -> None:
    from Sprout.execution.models import ApplyResult, ChangeProposal, ChangeProposalStatus
    from Sprout.runtime.changes import ChangeProposalService
    from Sprout.storage.bundle import StorageBundle
    from Sprout.storage.local.sqlite.metadata import open_metadata_store
    from Sprout.task.models import Task
    from Sprout.workspace.models import Workspace, WorkspaceKind

    storage = StorageBundle.in_memory()
    store = open_metadata_store(str(tmp_path / "core.db"))
    task = Task(id="task", workspace_id="ws")
    await store.save_task(task)
    await store.save_workspace(Workspace(
        id="ws", root=tmp_path, kind=WorkspaceKind.LOCAL_DIRECTORY,
    ))
    proposal = ChangeProposal(task_id=task.id, status=ChangeProposalStatus.APPROVED)
    await store.save_change_proposal(proposal)

    class Broker:
        calls = 0

        async def apply(self, proposal, workspace, **kwargs):
            self.calls += 1
            stored = await store.get_change_proposal(proposal.id)
            assert stored.metadata["verification_override"]["reason"] == "Emergency patch"
            return ApplyResult(proposal_id=proposal.id, applied=True)

    broker = Broker()
    service = ChangeProposalService(store, storage.operational, broker)
    try:
        assert not (await service.apply(proposal.id)).applied
        assert not (await service.apply(proposal.id, force=True)).applied
        assert broker.calls == 0
        assert (await service.apply(
            proposal.id, force=True, override_reason="Emergency patch", decided_by="alice",
        )).applied
        stored = await store.get_change_proposal(proposal.id)
        assert stored.metadata["verification_override"]["decided_by"] == "alice"
        assert stored.metadata["verification_override"]["verified"] is False
        assert (await store.get_task(task.id)).status is task.status
    finally:
        store.close()
