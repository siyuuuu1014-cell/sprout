"""Internal isolated commit coordinator; not exposed as a CLI or model tool."""

import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from Sprout.evolution.coding_assembly import build_coding_session_factory, coding_task_binding
from Sprout.evolution.replay_plan import verify_plan
from Sprout.execution.git_transaction_contract import fingerprint, validate_commit_write_contract
from Sprout.execution.isolated_git_authorization import (
    commit_approval_blockers,
    consume_commit_approval,
    git_approval_arguments,
)
from Sprout.execution.isolated_git_evidence import _inspect
from Sprout.execution.isolated_git_plan import IsolatedGitPlan
from Sprout.execution.isolated_git_writer import _write_commit
from Sprout.gateway.local_identity import current_cli_principal
from Sprout.orchestration.isolated_worker import _check_components, _check_experiment_components


async def _review_context(runtime, task_id, proposal_id, *, review_only=False):
    metadata = runtime.storage.metadata
    task = await metadata.get_task(task_id)
    if task is None:
        raise ValueError("Persisted experiment task required")
    payload = verify_plan(task.metadata.get("replay_plan"))
    if getattr(runtime, "_isolated_worker_role", None) == "commit_only":
        _check_experiment_components(runtime, payload)
    else:
        _check_components(runtime, payload)
    build_coding_session_factory(runtime, task)
    if getattr(runtime, "_isolated_worker_task_binding", None) != coding_task_binding(task):
        raise ValueError("Dedicated worker task binding required")
    workspace = await metadata.get_workspace(task.workspace_id)
    proposal = await metadata.get_change_proposal(proposal_id)
    if (workspace is None or proposal is None or proposal.task_id != task_id
            or proposal.sandbox_ref is None
            or proposal.status.value not in ({"pending", "approved"} if review_only
                                             else {"approved"})):
        raise ValueError("Persisted reviewable experiment proposal required")
    planner = IsolatedGitPlan(workspace, original=Path(payload["original_root"]),
                              evidence=Path(payload["repository_root"]).parent)
    plan = await planner.commit_plan(proposal.sandbox_ref.root)
    return task, payload, proposal, planner, plan


async def _admit(runtime, task_id, proposal_id, evaluation_node_id, contract, *, review_only=False):
    task, payload, proposal, planner, plan = await _review_context(
        runtime, task_id, proposal_id, review_only=review_only)
    metadata = runtime.storage.metadata
    validate_commit_write_contract(contract, plan)
    reviewed = "".join(item.diff_text for item in proposal.diffs)
    if hashlib.sha256(reviewed.encode()).hexdigest() != plan["diff_sha256"]:
        raise ValueError("Reviewed proposal diff does not match current Git plan")
    evidence = await _inspect(metadata, task_id, evaluation_node_id, planner, plan)
    snapshot = await metadata.get_git_proposal_snapshot(proposal_id)
    if (snapshot is None or snapshot["status"] != proposal.status.value
            or hashlib.sha256("".join(item["diff_text"] for item in
                json.loads(snapshot["diffs_json"])).encode()).hexdigest() != plan["diff_sha256"]):
        raise ValueError("Persisted proposal snapshot changed during evidence inspection")
    arguments = git_approval_arguments(task_id=task_id, proposal_id=proposal_id,
        operation="commit", replay_fingerprint=task.metadata["replay_plan"]["fingerprint"],
        git_plan=plan, proposal_fingerprint=fingerprint(snapshot),
        verification_fingerprint=fingerprint(evidence), write_contract=contract)
    return task, payload, planner, plan, snapshot, arguments


async def _commit_experiment(runtime, *, task_id, proposal_id, evaluation_node_id,
                             node_id, owner, approval_id, contract):
    """Explicit host-only call. No force, rollback, takeover or retry path.

    Runtime commissioning remains the caller's responsibility. This function
    rechecks current assembly and raw observations; it does not establish OS or
    image supply-chain trust or publish/validate a growth candidate.
    """
    metadata, operational = runtime.storage.metadata, runtime.storage.operational
    contract = deepcopy(contract)
    task, payload, planner, plan, snapshot, arguments = await _admit(
        runtime, task_id, proposal_id, evaluation_node_id, contract)
    principal = current_cli_principal()
    approval = await operational.get_approval(approval_id)
    blockers = commit_approval_blockers(approval, arguments, git_plan=plan,
        write_contract=contract, creator=payload["creator"], principal=principal)
    if blockers:
        return {"applied": False, "blockers": blockers}
    transaction_id = str(uuid5(NAMESPACE_URL, "isolated-commit:" + proposal_id))
    record = {"id": transaction_id, "proposal_id": proposal_id, "workspace_id": task.workspace_id,
        "task_id": task_id, "node_id": node_id, "owner": owner, "approval_id": approval_id,
        "target": plan["target"], "replay_fingerprint": arguments["replay_fingerprint"],
        "operation": "commit", "plan": plan, "plan_fingerprint": fingerprint(plan),
        "proposal_fingerprint": fingerprint(snapshot), "write_contract": contract,
        "approval_arguments": arguments, "evaluation_node_id": evaluation_node_id}
    if not await metadata.claim_git_transaction(record, snapshot):
        return {"applied": False, "blockers": ["git_transaction_already_owned_or_binding_changed"]}
    try:
        # Re-read raw observations and runtime bindings after acquiring ownership.
        admitted = await _admit(runtime, task_id, proposal_id, evaluation_node_id, contract)
        if admitted[3:] != (plan, snapshot, arguments):
            raise ValueError("Commit binding changed after claim")
        consumed = await consume_commit_approval(operational, approval_id=approval_id,
            arguments=arguments, git_plan=plan, write_contract=contract,
            creator=payload["creator"], principal=current_cli_principal())
        if not consumed["consumed"]:
            raise ValueError("Exact approval consumption failed")
        if not await metadata.begin_git_commit_write(transaction_id, owner):
            raise ValueError("One-shot write start rejected")
        observed = await _write_commit(planner, plan, contract)
        if not await metadata.settle_git_commit_proposal(transaction_id, owner, observed):
            raise ValueError("Observed commit requires manual state reconciliation")
        return {"applied": True, "transaction_id": transaction_id, **observed,
                "validation_committed": False}
    except BaseException:
        # If this persistence also fails, active ownership is still non-expiring.
        await asyncio.shield(metadata.finish_git_transaction(transaction_id, owner, "uncertain",
            {"reason": "commit_interrupted_or_unconfirmed", "automatic_retry": False}))
        raise
