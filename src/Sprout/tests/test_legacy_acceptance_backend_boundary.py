"""Legacy Local evidence must not gain authority by changing Runtime backend.

Only an in-memory test store and mock ApplyBroker are used. The historical
Local provenance is known to this fixture, not invented in persisted metadata.
"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution import acceptance
from Sprout.execution.models import ApplyResult, ChangeProposalStatus
from Sprout.execution.verification import proposal_verification_report
from Sprout.orchestration.terminal.temporal import TemporalOrchestratorBackend
from Sprout.runtime.nodes import NodeExecutor
from Sprout.runtime.runtime import Runtime
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.task.models import Task
from Sprout.tests.test_local_acceptance_apply import proposal
from Sprout.workspace.models import Workspace


async def historical_runtime(tmp_path, marker):
    item = proposal(None)
    verification = dict(item.metadata["verification"])
    verification.pop("local_baseline_valid")
    item = replace(item, status=ChangeProposalStatus.PENDING,
                   metadata={"verification": verification})
    task = Task(id="task", workspace_id="workspace",
                metadata={} if marker is None else {marker: {}})
    records = {item.id: item}

    async def save(value):
        records[value.id] = value

    metadata = SimpleNamespace(
        get_change_proposal=AsyncMock(side_effect=lambda key: records.get(key)),
        save_change_proposal=AsyncMock(side_effect=save),
        get_task=AsyncMock(return_value=task),
        get_workspace=AsyncMock(return_value=Workspace(id="workspace", root=tmp_path)),
        list_change_proposals=AsyncMock(side_effect=lambda _: list(records.values())),
    )
    runtime = object.__new__(Runtime)
    runtime.storage = SimpleNamespace(metadata=metadata, operational=MemoryOperationalStore())
    runtime.events = None
    runtime._approvals = None
    runtime._orchestrator_backend = None
    runtime._isolated_coding_required = False
    runtime._apply_broker = SimpleNamespace(
        apply=AsyncMock(return_value=ApplyResult(item.id, False, "mock: no repository I/O")))
    # Real existing approval lifecycle; the in-memory record is reused unchanged.
    approved = await runtime._changes().approve(item.id, decided_by="test-human")
    assert approved.status is ChangeProposalStatus.APPROVED
    assert proposal_verification_report(approved).verified is True
    assert "local_executor" not in approved.metadata["verification"]
    assert "local_baseline_valid" not in approved.metadata["verification"]
    assert "verification_override" not in approved.metadata
    return runtime, approved, records


def configure(runtime, backend):
    if backend == "temporal":
        # No Temporal connection or worker is created. Only Runtime's actual
        # backend-type dispatch is exercised.
        runtime._orchestrator_backend = object.__new__(TemporalOrchestratorBackend)
    runtime._isolated_coding_required = backend == "isolated_config"


@pytest.mark.parametrize("backend", ["local", "temporal", "isolated_config"])
@pytest.mark.parametrize("marker", [None, "replay_plan", "replay_source_task_id"])
async def test_public_apply_rejects_legacy_local_after_backend_change(tmp_path, backend, marker):
    runtime, approved, records = await historical_runtime(tmp_path, marker)
    # Establish Local rejection, then restore only configuration, not evidence.
    local = await runtime.apply_change_proposal(approved.id)
    assert local.applied is False
    runtime._apply_broker.apply.assert_not_awaited()
    configure(runtime, backend)
    result = await runtime.apply_change_proposal(approved.id)
    assert result.applied is False
    assert records[approved.id] == approved
    assert "verification_override" not in records[approved.id].metadata
    runtime._apply_broker.apply.assert_not_awaited()


@pytest.mark.parametrize("local_available", [True, False])
@pytest.mark.parametrize("state", ["match", "mismatch", "unknown"])
@pytest.mark.parametrize("route", ["standalone", "node_service", "node_direct"])
async def test_all_apply_routes_share_frozen_evidence_gate(
    tmp_path, local_available, state, route,
):
    runtime, approved, records = await historical_runtime(tmp_path, None)
    binding = acceptance.local_executor_binding()
    verification = dict(approved.metadata["verification"])
    if state != "unknown":
        verification.update(local_baseline_valid=True, local_executor=(
            binding if state == "match" else {**binding, "fingerprint": "0" * 64}))
    item = replace(approved, metadata={"verification": verification})
    records[item.id] = item
    service = runtime._changes(local_acceptance=local_available)
    expected = local_available and state == "match"
    runtime._apply_broker.apply.return_value = ApplyResult(item.id, True, "mock success")
    if route == "standalone":
        result = await service.apply(item.id)
        assert result.applied is expected
    else:
        executor = object.__new__(NodeExecutor)
        executor._metadata = runtime.storage.metadata
        executor._local_acceptance = local_available
        executor._isolated_coding_required = False
        executor._changes = service if route == "node_service" else None
        executor._apply_broker = runtime._apply_broker
        result = await executor._apply(
            await runtime.storage.metadata.get_workspace("workspace"),
            await runtime.storage.metadata.get_task("task"))
        assert result["applied"] is expected
    assert runtime._apply_broker.apply.await_count == int(expected)
    if not expected:
        assert records[item.id] == item


@pytest.mark.parametrize("backend", ["local", "temporal", "isolated_config"])
async def test_explicit_force_keeps_unknown_and_original_reason(tmp_path, backend):
    runtime, approved, records = await historical_runtime(tmp_path, None)
    configure(runtime, backend)
    result = await runtime.apply_change_proposal(
        approved.id, force=True, override_reason="human waiver", decided_by="test-human")
    assert result.applied is False  # Mock intentionally makes no repository change.
    runtime._apply_broker.apply.assert_awaited_once()
    saved = records[approved.id]
    audit = saved.metadata["verification_override"]
    assert audit["reason"] == "human waiver"
    assert audit["decided_by"] == "test-human"
    assert audit["verified"] is False and "unknown" in audit["blocker"]
    assert saved.metadata["verification"] == approved.metadata["verification"]
    report = await runtime.get_verification_report("task")
    assert report["verified"] is False


@pytest.mark.parametrize("claim", [
    {"backend": "temporal", "trusted": True},
    {"source": "docker", "trusted_nonlocal": True},
    {"acceptance_fingerprint": "legacy-fingerprint"},
])
def test_unproven_nonlocal_claims_are_unknown(claim):
    verification = {"status": "passed", **claim}
    for available in (True, False):
        assert acceptance.verification_evidence_applicability(
            verification, local_execution_available=available) == "unknown"


@pytest.mark.parametrize("backend", ["local", "temporal", "isolated_config"])
async def test_public_report_does_not_revalidate_legacy_local_on_backend_change(tmp_path, backend):
    runtime, approved, records = await historical_runtime(tmp_path, None)
    local = await runtime.get_verification_report("task")
    assert local["verified"] is False
    assert local["historical_verification"]["verified"] is True
    configure(runtime, backend)
    report = await runtime.get_verification_report("task")
    assert records[approved.id] == approved
    assert report["verified"] is False
    assert report["current_applicability"] == "unknown"
    assert report["historical_verification"]["verified"] is True
    runtime._apply_broker.apply.assert_not_awaited()


@pytest.mark.parametrize("backend", ["local", "temporal", "isolated_config"])
async def test_public_apply_and_report_agree_on_bound_local_in_current_context(tmp_path, backend):
    runtime, approved, records = await historical_runtime(tmp_path, None)
    verification = {**approved.metadata["verification"], "local_baseline_valid": True,
                    "local_executor": acceptance.local_executor_binding()}
    records[approved.id] = replace(approved, metadata={"verification": verification})
    configure(runtime, backend)
    report = await runtime.get_verification_report("task")
    assert report["historical_verification"]["verified"] is True
    assert report["verified"] == (backend == "local")
    await runtime.apply_change_proposal(approved.id)
    assert runtime._apply_broker.apply.await_count == int(backend == "local")
