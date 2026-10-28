"""F3 safety boundary diagnostics. No Git, process, model, or project execution."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.host_acceptance import check_module_dependencies
from Sprout.execution.models import ApplyResult, ChangeProposal, ChangeProposalStatus
from Sprout.execution.models import TestResult as Check
from Sprout.execution.verification import proposal_verification_report
from Sprout.runtime.changes import ChangeProposalService
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import Task


@pytest.mark.parametrize("source", [
    "import os\ndef run(value):\n    os.unlink('outside_scope')\n    return value\n",
    "def run(value):\n    open('outside_scope', 'w').write('side effect')\n    return value\n",
])
def test_untrusted_python_effects_require_execution_boundary(source):
    """RED: dependency closure is not effect confinement. Never execute this text."""
    spec = AcceptanceSpec.from_payload({"cases": [dict(id="case", target="app:run",
                                                     args=[1], expected_output=1)]})
    with pytest.raises(PermissionError):
        check_module_dependencies(spec, {"app.py": source})


def fixture():
    names = ("test: pytest", "code: pytest -q", "check: compileall", "check: ruff", "poc: demo")
    tests = tuple(Check(name, False, executed=False) for name in names)
    tests += (Check("acceptance:normal: frozen", True),)
    spec = AcceptanceSpec.from_payload({"cases": [dict(id="normal", target="app:run",
                                                     args=[1], expected_output=1)]})
    return ChangeProposal(task_id="task", test_results=tests, metadata={"verification": {
        "status": "partially_verified", "acceptance_spec": spec.to_payload(),
        "demo_path": "examples/demo.py", "local_baseline_valid": True}})


@pytest.mark.parametrize("approval", list(ChangeProposalStatus))
def test_approval_state_does_not_create_verification_evidence(approval):
    proposal = replace(fixture(), status=approval)
    report = proposal_verification_report(proposal)
    assert not report.verified
    assert len([c for c in report.checks if not c.executed]) == 5
    assert "incomplete" in report.blocker.lower()


@pytest.mark.parametrize("status", ["partially_verified", "passed"])
async def test_incomplete_normal_apply_never_calls_broker(monkeypatch, status):
    proposal = fixture()
    proposal = replace(proposal, status=ChangeProposalStatus.APPROVED, metadata={
        "verification": {**proposal.metadata["verification"], "status": status}})
    # Isolate the verification gate: even a matching environment cannot excuse missing checks.
    monkeypatch.setattr("Sprout.runtime.changes.verification_evidence_applicability",
                        lambda *args, **kwargs: "local_match")
    metadata = SimpleNamespace(get_change_proposal=AsyncMock(return_value=proposal),
        get_task=AsyncMock(return_value=Task(id="task", workspace_id="workspace")),
        get_workspace=AsyncMock(), save_change_proposal=AsyncMock())
    broker = SimpleNamespace(apply=AsyncMock())
    service = ChangeProposalService(metadata, MemoryOperationalStore(), broker)
    result = await service.apply(proposal.id)
    assert result.applied is False and "incomplete" in result.reason.lower()
    broker.apply.assert_not_called()
    metadata.save_change_proposal.assert_not_called()
    metadata.get_workspace.assert_not_called()


async def test_complete_checks_can_reach_mock_gate_without_real_apply(monkeypatch):
    proposal = fixture()
    checks = tuple(replace(check, executed=True, passed=True) for check in proposal.test_results)
    proposal = replace(proposal, status=ChangeProposalStatus.APPROVED, test_results=checks,
        metadata={"verification": {**proposal.metadata["verification"], "status": "passed"}})
    assert proposal_verification_report(proposal).verified
    monkeypatch.setattr("Sprout.runtime.changes.verification_evidence_applicability",
                        lambda *args, **kwargs: "local_match")
    task = Task(id="task", workspace_id="workspace")
    metadata = SimpleNamespace(get_change_proposal=AsyncMock(return_value=proposal),
        get_task=AsyncMock(return_value=task), get_workspace=AsyncMock(return_value=object()))
    broker = SimpleNamespace(apply=AsyncMock(return_value=ApplyResult(proposal.id, False,
                                                                    "fixture stop")))
    service = ChangeProposalService(metadata, MemoryOperationalStore(), broker)
    result = await service.apply(proposal.id)
    assert not result.applied
    broker.apply.assert_awaited_once()
