from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.git_transaction_contract import commit_write_contract
from Sprout.execution.isolated_git_authorization import (
    commit_approval_blockers,
    git_approval_arguments,
    git_approval_blockers,
    inspect_git_approval,
)
from Sprout.gateway.identity import Principal
from Sprout.security.approval import ApprovalManager, ApprovalRecord, ApprovalStatus
from Sprout.storage.local.memory.operational import MemoryOperationalStore


def fixture():
    creator = {"user_id": "owner", "source": "cli", "authenticated": True}
    principal = Principal(user_id="owner", source="cli", authenticated=True)
    arguments = git_approval_arguments(
        task_id="task", proposal_id="proposal", operation="commit", replay_fingerprint="replay",
        git_plan={"operation": "commit", "target": "experiment/repo", "write_authorized": False,
                  "base_commit": "a" * 40, "source_manifest": {"app.py": "b" * 64}},
        proposal_fingerprint="proposal-hash", verification_fingerprint="verification-hash")
    record = ApprovalRecord(
        tool="git.commit", arguments_fingerprint=ApprovalManager.fingerprint(arguments),
        action_hash=ApprovalManager.fingerprint({"tool": "git.commit", **arguments}),
        task_id="task", resource_scope="proposal", requested_by="owner", decided_by="owner",
        source="cli", status=ApprovalStatus.APPROVED, decided_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1))
    return arguments, record, creator, principal


@pytest.mark.parametrize("field,value", [
    ("status", ApprovalStatus.PENDING), ("single_use", False), ("task_id", "other"),
    ("resource_scope", "other"), ("decided_by", "other"), ("requested_by", "other"),
    ("source", "web"), ("expires_at", None), ("used_at", datetime.now(UTC)),
    ("expires_at", datetime.now(UTC) - timedelta(seconds=1)),
])
def test_legacy_wrong_scope_expired_or_reused_grants_block(field, value):
    arguments, record, creator, principal = fixture()
    record = replace(record, **{field: value})
    assert git_approval_blockers(record, arguments, creator=creator, principal=principal)


@pytest.mark.parametrize("field", ["verification_fingerprint", "proposal_fingerprint",
                                   "git_plan_fingerprint", "operation", "target"])
def test_action_drift_cannot_reuse_grant(field):
    arguments, record, creator, principal = fixture()
    arguments[field] = "rollback" if field == "operation" else "changed"
    assert "exact_git_approval_fingerprint_mismatch" in git_approval_blockers(
        record, arguments, creator=creator, principal=principal)


def test_other_operator_cannot_use_creator_approval():
    arguments, record, creator, _ = fixture()
    other = Principal(user_id="other", source="cli", authenticated=True)
    assert "current_operator_not_experiment_creator" in git_approval_blockers(
        record, arguments, creator=creator, principal=other)


async def test_exact_persisted_approval_still_does_not_authorize_writes_or_consume():
    arguments, _, creator, principal = fixture()
    operational = MemoryOperationalStore()
    manager = ApprovalManager(operational)
    record = await manager.request("git.commit", arguments, task_id="task",
                                   resource_scope="proposal", requested_by="owner", source="cli")
    record = await manager.decide(record.id, approved=True, decided_by="owner")
    output = await inspect_git_approval(operational, approval_id=record.id, arguments=arguments,
                                       creator=creator, principal=principal)
    assert output["approval_binding_passed"] is True
    assert output["write_authorized"] is False
    assert "isolated_git_measurement_handoff_not_established" in output["blockers"]
    assert record.status is ApprovalStatus.APPROVED and record.used_at is None
    stored = await operational.get_approval(record.id)
    assert stored.status is ApprovalStatus.APPROVED and stored.used_at is None


async def test_missing_record_cannot_be_replaced_by_imported_approved_flag():
    arguments, _, creator, principal = fixture()
    arguments["approved"] = True
    operational = SimpleNamespace(get_approval=AsyncMock(return_value=None))
    output = await inspect_git_approval(operational, approval_id="missing", arguments=arguments,
                                       creator=creator, principal=principal)
    assert output["approval_binding_passed"] is False and output["write_authorized"] is False
    assert "exact_git_approval_arguments_invalid" in output["blockers"]


def test_missing_persisted_approval_blocks_valid_arguments():
    arguments, _, creator, principal = fixture()
    assert "exact_git_approval_missing" in git_approval_blockers(
        None, arguments, creator=creator, principal=principal)


def write_fixture():
    _, record, creator, principal = fixture()
    plan = {"operation": "commit", "target": "experiment/repo", "write_authorized": False,
            "base_commit": "a" * 40, "source_manifest": {"app.py": "b" * 64}}
    contract = commit_write_contract(plan, author_name="Fixture",
        author_email="fixture@invalid.example", message="Explicit fixture commit")
    arguments = git_approval_arguments(task_id="task", proposal_id="proposal", operation="commit",
        replay_fingerprint="replay", git_plan=plan, proposal_fingerprint="proposal-hash",
        verification_fingerprint="verification-hash", write_contract=contract)
    record = replace(record, arguments_fingerprint=ApprovalManager.fingerprint(arguments),
                     action_hash=ApprovalManager.fingerprint({"tool": "git.commit", **arguments}))
    return plan, contract, arguments, record, creator, principal


def test_new_commit_approval_binds_exact_operator_options():
    plan, contract, arguments, record, creator, principal = write_fixture()
    assert arguments["schema_version"] == 2
    assert not commit_approval_blockers(record, arguments, git_plan=plan, write_contract=contract,
                                        creator=creator, principal=principal)


@pytest.mark.parametrize("field,value", [("author_name", "Different"),
    ("author_email", "different@invalid.example"), ("message", "different commit")])
def test_commit_identity_or_message_drift_cannot_reuse_approval(field, value):
    plan, contract, arguments, record, creator, principal = write_fixture()
    contract[field] = value
    assert "exact_commit_write_contract_approval_required" in commit_approval_blockers(
        record, arguments, git_plan=plan, write_contract=contract,
        creator=creator, principal=principal)


def test_legacy_readonly_grant_cannot_authorize_commit_options():
    plan, contract, _, _, creator, principal = write_fixture()
    arguments, record, _, _ = fixture()
    assert "exact_commit_write_contract_approval_required" in commit_approval_blockers(
        record, arguments, git_plan=plan, write_contract=contract,
        creator=creator, principal=principal)


@pytest.mark.parametrize("field,value", [("author_name", ""), ("author_name", "name\nextra"),
    ("author_name", "name <fake>"), ("author_email", "not-an-email"), ("message", "message\x00")])
def test_missing_or_ambiguous_operator_options_do_not_produce_contract(field, value):
    plan, _, _, _, _, _ = write_fixture()
    values = {"author_name": "Fixture", "author_email": "fixture@invalid.example",
              "message": "commit"}
    values[field] = value
    with pytest.raises(ValueError):
        commit_write_contract(plan, **values)
