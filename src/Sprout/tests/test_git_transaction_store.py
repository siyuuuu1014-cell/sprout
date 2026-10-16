import asyncio
from dataclasses import replace

import pytest

from Sprout.execution.models import ChangeProposal, ChangeProposalStatus
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.storage.local.sqlite.git_transaction import fingerprint
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus
from Sprout.workspace.models import Workspace


async def fixture(tmp_path):
    path = tmp_path / "metadata.db"
    store = open_metadata_store(str(path))
    workspace = Workspace(id="experiment", root=tmp_path / "repo")
    await store.save_workspace(workspace)
    task = Task(workspace_id=workspace.id, status=TaskStatus.RUNNING, metadata={
        "execution_mode": "code", "replay_plan": {
            "fingerprint": "replay", "payload": {"schema_version": 5}}})
    await store.save_task(task)
    node = ExecutionNode(task_id=task.id, type=NodeType.APPLY)
    await store.save_execution_node(node)
    assert await store.claim_execution_node(node, "owner", "")
    proposal = ChangeProposal(task_id=task.id, status=ChangeProposalStatus.APPROVED)
    await store.save_change_proposal(proposal)
    snapshot = await store.get_git_proposal_snapshot(proposal.id)
    plan = {"operation": "commit", "target": str(workspace.root),
            "base_commit": "a" * 40, "write_authorized": False}
    record = {"id": "transaction", "proposal_id": proposal.id, "task_id": task.id,
              "workspace_id": workspace.id, "node_id": node.id, "owner": "owner",
              "approval_id": "audit-reference-not-proof", "target": str(workspace.root),
              "replay_fingerprint": "replay", "operation": "commit", "plan": plan,
              "plan_fingerprint": fingerprint(plan), "proposal_fingerprint": fingerprint(snapshot)}
    return path, store, proposal, record, snapshot


async def test_atomic_claim_and_unknown_owner_survives_restart(tmp_path):
    path, store, _, record, snapshot = await fixture(tmp_path)
    other = open_metadata_store(str(path))
    try:
        claims = await asyncio.gather(store.claim_git_transaction(record, snapshot),
                                      other.claim_git_transaction(record, snapshot))
        assert sorted(claims) == [False, True]
        assert not await store.finish_git_transaction("transaction", "wrong", "uncertain", {})
        assert await store.finish_git_transaction("transaction", "owner", "uncertain", {})
    finally:
        store.close()
        other.close()
    reopened = open_metadata_store(str(path))
    try:
        assert (await reopened.get_git_transaction("transaction"))["state"] == "uncertain"
        assert (await reopened.get_git_transaction("transaction"))["proposal_snapshot"] == snapshot
        assert not await reopened.claim_git_transaction(record, snapshot)
        assert not await reopened.finish_git_transaction("transaction", "owner", "completed",
                                                        {"base_commit": "a" * 40,
                                                         "result_commit": "b" * 40})
    finally:
        reopened.close()


@pytest.mark.parametrize("change", ["proposal", "owner", "target", "replay", "cancelled"])
async def test_stale_binding_or_authority_blocks_claim(tmp_path, change):
    _, store, proposal, record, snapshot = await fixture(tmp_path)
    try:
        if change == "proposal":
            await store.save_change_proposal(replace(proposal, metadata={"changed": True}))
        elif change == "owner":
            record["owner"] = "wrong"
        elif change == "target":
            record["target"] = record["plan"]["target"] = "other"
            record["plan_fingerprint"] = fingerprint(record["plan"])
        elif change == "replay":
            record["replay_fingerprint"] = "other"
        else:
            await store.update_task_status(record["task_id"], TaskStatus.CANCELLED)
        assert not await store.claim_git_transaction(record, snapshot)
    finally:
        store.close()


async def test_rollback_claim_requires_applied_proposal_and_settles_against_applied_head(tmp_path):
    _, store, proposal, record, snapshot = await fixture(tmp_path)
    try:
        plan = {**record["plan"], "operation": "rollback", "applied_commit": "b" * 40}
        record = {**record, "operation": "rollback", "plan": plan,
                  "plan_fingerprint": fingerprint(plan)}
        assert not await store.claim_git_transaction(record, snapshot)
        await store.save_change_proposal(replace(proposal, status=ChangeProposalStatus.APPLIED))
        snapshot = await store.get_git_proposal_snapshot(proposal.id)
        record["proposal_fingerprint"] = fingerprint(snapshot)
        assert await store.claim_git_transaction(record, snapshot)
        assert await store.finish_git_transaction(record["id"], "owner", "completed",
                                                  {"base_commit": "b" * 40,
                                                   "result_commit": "c" * 40})
        assert (await store.get_change_proposal(proposal.id)).status is ChangeProposalStatus.APPLIED
    finally:
        store.close()


async def test_unknown_transaction_blocks_other_proposal_in_same_workspace(tmp_path):
    _, store, proposal, record, snapshot = await fixture(tmp_path)
    try:
        assert await store.claim_git_transaction(record, snapshot)
        assert await store.finish_git_transaction(record["id"], "owner", "uncertain", {})
        other = replace(proposal, id="other-proposal")
        await store.save_change_proposal(other)
        second_snapshot = await store.get_git_proposal_snapshot(other.id)
        second = {**record, "id": "other-transaction", "proposal_id": other.id,
                  "proposal_fingerprint": fingerprint(second_snapshot)}
        assert not await store.claim_git_transaction(second, second_snapshot)
    finally:
        store.close()


async def test_settlement_requires_new_observed_commit_and_cannot_release(tmp_path):
    _, store, _, record, snapshot = await fixture(tmp_path)
    try:
        assert await store.claim_git_transaction(record, snapshot)
        with pytest.raises(ValueError, match="no reset"):
            await store.finish_git_transaction(record["id"], "owner", "not_started", {})
        with pytest.raises(ValueError, match="frozen base"):
            await store.finish_git_transaction(record["id"], "owner", "completed",
                                               {"base_commit": "c" * 40,
                                                "result_commit": "b" * 40})
        assert await store.finish_git_transaction(record["id"], "owner", "completed",
                                                  {"base_commit": "a" * 40,
                                                   "result_commit": "b" * 40})
        assert not await store.claim_git_transaction(record, snapshot)
        # A storage settlement is not proposal apply or validation authority.
        proposal = await store.get_change_proposal(record["proposal_id"])
        assert proposal.status is ChangeProposalStatus.APPROVED
    finally:
        store.close()


async def test_commit_and_proposal_settle_together_without_validating_task(tmp_path):
    _, store, proposal, record, snapshot = await fixture(tmp_path)
    try:
        assert await store.claim_git_transaction(record, snapshot)
        result = {"base_commit": "a" * 40, "result_commit": "b" * 40}
        assert await store.settle_git_commit_proposal(record["id"], "owner", result)
        settled = await store.get_git_transaction(record["id"])
        updated = await store.get_change_proposal(proposal.id)
        assert settled["state"] == "completed" and settled["result"] == result
        assert updated.status is ChangeProposalStatus.APPLIED
        assert updated.metadata["base_commit"] == result["base_commit"]
        assert updated.metadata["applied_commit"] == result["result_commit"]
        assert updated.metadata["isolated_git_transaction_id"] == record["id"]
        assert (await store.get_task(record["task_id"])).status == TaskStatus.RUNNING
        assert not await store.settle_git_commit_proposal(record["id"], "owner", result)
    finally:
        store.close()


@pytest.mark.parametrize("change", ["owner", "proposal", "cancelled", "node"])
async def test_joint_settlement_cas_failure_keeps_both_records_unsettled(tmp_path, change):
    _, store, proposal, record, snapshot = await fixture(tmp_path)
    try:
        assert await store.claim_git_transaction(record, snapshot)
        owner = "owner"
        if change == "owner":
            owner = "different"
        elif change == "proposal":
            await store.save_change_proposal(replace(proposal, metadata={"changed": True}))
        elif change == "cancelled":
            await store.update_task_status(record["task_id"], TaskStatus.CANCELLED)
        else:
            node = await store.get_execution_node(record["node_id"])
            assert await store.finish_execution_node(
                replace(node, status=NodeStatus.COMPLETED), "owner", "completed")
        assert not await store.settle_git_commit_proposal(record["id"], owner,
            {"base_commit": "a" * 40, "result_commit": "b" * 40})
        assert (await store.get_git_transaction(record["id"]))["state"] == "active"
        updated = await store.get_change_proposal(proposal.id)
        assert updated.status is ChangeProposalStatus.APPROVED
    finally:
        store.close()


async def test_joint_settlement_rolls_back_proposal_when_second_write_fails(tmp_path):
    import sqlite3

    _, store, proposal, record, snapshot = await fixture(tmp_path)
    try:
        assert await store.claim_git_transaction(record, snapshot)
        # Fault injection in this test-owned database only.
        store._db.executescript("CREATE TRIGGER fail_git_settlement BEFORE UPDATE "
            "ON isolated_git_transactions WHEN NEW.state='completed' BEGIN "
            "SELECT RAISE(ABORT, 'test settlement fault'); END;")
        with pytest.raises(sqlite3.IntegrityError, match="test settlement fault"):
            await store.settle_git_commit_proposal(record["id"], "owner",
                {"base_commit": "a" * 40, "result_commit": "b" * 40})
        assert (await store.get_git_transaction(record["id"]))["state"] == "active"
        assert await store.get_git_proposal_snapshot(proposal.id) == snapshot
    finally:
        store.close()
