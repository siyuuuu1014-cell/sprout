import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.git_transaction_contract import commit_write_contract
from Sprout.execution.isolated_git_authorization import git_approval_arguments
from Sprout.execution.isolated_git_coordinator import _commit_experiment
from Sprout.gateway.identity import Principal
from Sprout.security.approval import ApprovalManager, ApprovalStatus
from Sprout.storage.local.memory.operational import MemoryOperationalStore
from Sprout.tests.test_git_transaction_store import fixture
from Sprout.tests.test_isolated_git_writer import prepared
from Sprout.workspace.models import Workspace


async def arranged(tmp_path, monkeypatch):
    _, metadata, proposal, record, snapshot = await fixture(tmp_path)
    (tmp_path / "git").mkdir()
    repo, _, planner, plan, _ = await prepared(tmp_path / "git")
    await metadata.save_workspace(Workspace(id=record["workspace_id"], root=repo))
    task = await metadata.get_task(record["task_id"])
    creator = {"user_id": "owner", "source": "cli", "authenticated": True}
    contract = commit_write_contract(plan, author_name="Explicit Operator",
        author_email="operator@invalid.example", message="Explicit reviewed commit")
    from Sprout.execution.git_transaction_contract import fingerprint

    arguments = git_approval_arguments(task_id=task.id, proposal_id=proposal.id,
        operation="commit", replay_fingerprint="replay", git_plan=plan,
        proposal_fingerprint=fingerprint(snapshot), verification_fingerprint="fixture-observation",
        write_contract=contract)
    operational = MemoryOperationalStore()
    manager = ApprovalManager(operational)
    approval = await manager.request("git.commit", arguments, task_id=task.id,
        requested_by="owner", source="cli", resource_scope=proposal.id)
    await manager.decide(approval.id, True, decided_by="owner")
    # Control-flow fixture only: production admission separately checks real
    # Runtime snapshots, proposal diff and raw container observations.
    admit = AsyncMock(return_value=(task, {"creator": creator}, planner, plan, snapshot, arguments))
    monkeypatch.setattr("Sprout.execution.isolated_git_coordinator._admit", admit)
    monkeypatch.setattr("Sprout.execution.isolated_git_coordinator.current_cli_principal",
        lambda: Principal(user_id="owner", source="cli", authenticated=True))
    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=metadata, operational=operational))
    kwargs = dict(task_id=task.id, proposal_id=proposal.id, evaluation_node_id="evaluation",
        node_id=record["node_id"], owner="owner", approval_id=approval.id, contract=contract)
    return metadata, operational, repo, runtime, kwargs


async def test_coordinator_real_git_and_atomic_settlement(tmp_path, monkeypatch):
    metadata, operational, repo, runtime, kwargs = await arranged(tmp_path, monkeypatch)
    try:
        result = await _commit_experiment(runtime, **kwargs)
        assert result["applied"] and result["validation_committed"] is False
        assert (repo / "app.py").read_text() == "value = 2\n"
        transaction = await metadata.get_git_transaction(result["transaction_id"])
        assert transaction["state"] == "completed" and transaction["write_started"]
        approval = await operational.get_approval(kwargs["approval_id"])
        assert approval.status is ApprovalStatus.CONSUMED
        assert (await metadata.get_change_proposal(kwargs["proposal_id"])).status.value == "applied"
        assert not (await _commit_experiment(runtime, **kwargs))["applied"]
    finally:
        metadata.close()


async def test_writer_failure_retains_uncertain_claim_and_prevents_repeat(tmp_path, monkeypatch):
    metadata, _, _, runtime, kwargs = await arranged(tmp_path, monkeypatch)
    write = AsyncMock(side_effect=RuntimeError("injected failure"))
    monkeypatch.setattr("Sprout.execution.isolated_git_coordinator._write_commit", write)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            await _commit_experiment(runtime, **kwargs)
        assert not (await _commit_experiment(runtime, **kwargs))["applied"]
        assert write.await_count == 1
        from uuid import NAMESPACE_URL, uuid5

        transaction = await metadata.get_git_transaction(
            str(uuid5(NAMESPACE_URL, "isolated-commit:" + kwargs["proposal_id"])))
        assert transaction["state"] == "uncertain" and transaction["write_started"]
        proposal = await metadata.get_change_proposal(kwargs["proposal_id"])
        assert proposal.status.value == "approved"
    finally:
        metadata.close()


async def test_two_coordinator_calls_only_write_once(tmp_path, monkeypatch):
    metadata, _, _, runtime, kwargs = await arranged(tmp_path, monkeypatch)
    from Sprout.execution.isolated_git_writer import _write_commit

    write = AsyncMock(wraps=_write_commit)
    monkeypatch.setattr("Sprout.execution.isolated_git_coordinator._write_commit", write)
    try:
        results = await asyncio.gather(_commit_experiment(runtime, **kwargs),
                                       _commit_experiment(runtime, **kwargs))
        assert sum(result["applied"] for result in results) == 1
        assert write.await_count == 1
    finally:
        metadata.close()


async def test_cancellation_retains_uncertain_ownership(tmp_path, monkeypatch):
    metadata, _, _, runtime, kwargs = await arranged(tmp_path, monkeypatch)
    write = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr("Sprout.execution.isolated_git_coordinator._write_commit", write)
    try:
        with pytest.raises(asyncio.CancelledError):
            await _commit_experiment(runtime, **kwargs)
        assert not (await _commit_experiment(runtime, **kwargs))["applied"]
        assert write.await_count == 1
    finally:
        metadata.close()
