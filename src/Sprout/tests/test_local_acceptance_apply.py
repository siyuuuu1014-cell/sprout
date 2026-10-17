"""Public service, graph compatibility path and read projections fail closed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution import acceptance
from Sprout.execution.models import ApplyResult, ChangeProposal, ChangeProposalStatus
from Sprout.execution.models import TestResult as CheckResult
from Sprout.runtime.changes import ChangeProposalService
from Sprout.runtime.nodes import NodeExecutor
from Sprout.runtime.runtime import Runtime
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.task.models import Task
from Sprout.tests.test_local_acceptance_execution import contract


def proposal(binding):
    return ChangeProposal(task_id="task", status=ChangeProposalStatus.APPROVED,
        test_results=(CheckResult("acceptance:normal: python", True),
                      CheckResult("poc: demo", True)),
        metadata={"verification": {"acceptance_spec": contract().to_payload(), "status": "passed",
            "demo_path": "examples/demo.py", "local_baseline_valid": True,
            **({"local_executor": binding} if binding else {})}})


@pytest.mark.parametrize("state", ["match", "mismatch", "unknown"])
async def test_standalone_service_and_node_direct_apply_check_environment(state):
    binding = acceptance.local_executor_binding()
    frozen = (binding if state == "match" else
              {**binding, "fingerprint": "0" * 64} if state == "mismatch" else None)
    item = proposal(frozen)
    task = Task(id="task", workspace_id="workspace")
    metadata = SimpleNamespace(get_change_proposal=AsyncMock(return_value=item),
        get_task=AsyncMock(return_value=task), get_workspace=AsyncMock(return_value=object()),
        list_change_proposals=AsyncMock(return_value=[item]), save_change_proposal=AsyncMock())
    apply = SimpleNamespace(apply=AsyncMock(return_value=ApplyResult(item.id, False, "test stop")))
    service = ChangeProposalService(metadata, MemoryOperationalStore(), apply)
    result = await service.apply(item.id)
    assert not result.applied
    assert apply.apply.await_count == int(state == "match")
    metadata.save_change_proposal.assert_not_awaited()
    if state != "match":
        assert state in result.reason
        executor = object.__new__(NodeExecutor)
        executor._metadata = metadata
        executor._local_acceptance = True
        executor._isolated_coding_required = False
        result = await executor._apply(None, task)
        assert not result["applied"] and result["verified"] is False
        apply.apply.assert_not_awaited()


async def test_force_retains_unverified_environment_reason():
    item = proposal(None)
    task = Task(id="task", workspace_id="workspace")
    metadata = SimpleNamespace(get_change_proposal=AsyncMock(return_value=item),
        get_task=AsyncMock(return_value=task), get_workspace=AsyncMock(return_value=object()),
        save_change_proposal=AsyncMock())
    apply = SimpleNamespace(apply=AsyncMock(return_value=ApplyResult(item.id, False, "test stop")))
    service = ChangeProposalService(metadata, MemoryOperationalStore(), apply)
    await service.apply(item.id, force=True, override_reason="explicit waiver")
    saved = metadata.save_change_proposal.call_args.args[0]
    override = saved.metadata["verification_override"]
    assert override["verified"] is False and "unknown" in override["blocker"]


@pytest.mark.parametrize("state", ["match", "mismatch", "unknown"])
async def test_report_preserves_historical_pass_without_inheriting_applicability(state):
    binding = acceptance.local_executor_binding()
    frozen = (binding if state == "match" else
              {**binding, "fingerprint": "0" * 64} if state == "mismatch" else None)
    item = proposal(frozen)
    runtime = object.__new__(Runtime)
    runtime._orchestrator_backend = None
    runtime._isolated_coding_required = False
    runtime.get_task = AsyncMock(return_value=Task(id="task"))
    runtime.list_change_proposals = AsyncMock(return_value=[item])
    report = await runtime.get_verification_report("task")
    assert report["historical_verification"]["verified"] is True
    assert report["current_applicability"] == state
    assert report["verified"] == (state == "match")
    assert "verification_override" not in item.metadata


async def test_nonlocal_service_cannot_reinterpret_a_local_binding():
    item = proposal(acceptance.local_executor_binding())
    metadata = SimpleNamespace(get_change_proposal=AsyncMock(return_value=item),
        get_task=AsyncMock(return_value=Task(id="task")), save_change_proposal=AsyncMock())
    apply = SimpleNamespace(apply=AsyncMock())
    service = ChangeProposalService(metadata, MemoryOperationalStore(), apply,
                                    local_acceptance=False)
    result = await service.apply(item.id)
    assert not result.applied and "unknown" in result.reason
    apply.apply.assert_not_awaited()
    metadata.save_change_proposal.assert_not_awaited()
