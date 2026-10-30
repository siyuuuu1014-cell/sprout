"""SQLite Proposal data CAS only; no Receipt, approval or write authority is issued."""

import asyncio
from dataclasses import replace

import pytest

from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.execution.models import ChangeProposalStatus
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.tests.test_r04_e_apply_transactions import setup


@pytest.mark.parametrize("mode", ["concurrent", "patch", "status"])
async def test_exact_proposal_approval_cas(tmp_path, mode):
    path, store, record, _ = await setup(tmp_path)
    other = open_metadata_store(str(path))
    try:
        proposal = await store.get_change_proposal(record["proposal_id"])
        # A ledger payload exercises SQL equality, never a Runtime credential.
        payload = {"version": 1, "proposal_id": proposal.id, "task_id": proposal.task_id}
        proposal = replace(
            proposal,
            status=ChangeProposalStatus.PENDING,
            metadata={
                "apply_plan": payload,
                "apply_plan_sha256": fingerprint(payload),
                "review_only": False,
                "change_coverage": {"status": "complete_for_apply_plan"},
            },
        )
        await store.save_change_proposal(proposal)
        snapshot = await store.get_git_proposal_snapshot(proposal.id)
        if mode == "concurrent":
            results = await asyncio.gather(
                store.approve_apply_proposal(snapshot), other.approve_apply_proposal(snapshot)
            )
            assert sorted(results) == [False, True]
            assert (
                await store.get_change_proposal(proposal.id)
            ).status is ChangeProposalStatus.APPROVED
            assert not await store.approve_apply_proposal(snapshot)
        else:
            changed = (
                replace(proposal, metadata={**proposal.metadata, "changed": "patch"})
                if mode == "patch"
                else replace(proposal, status=ChangeProposalStatus.REJECTED)
            )
            await store.save_change_proposal(changed)
            assert not await store.approve_apply_proposal(snapshot)
            assert await store.get_change_proposal(proposal.id) == changed
    finally:
        store.close()
        other.close()
