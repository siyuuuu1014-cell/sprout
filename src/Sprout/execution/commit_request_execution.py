"""Host-only decision execution; durable node ownership before any Git authorization."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.execution.isolated_git_coordinator import _commit_experiment
from Sprout.gateway.local_identity import creator_blockers, current_cli_principal
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.runtime.experiment_commits import ExperimentCommitService
from Sprout.task.models import TaskStatus


async def _finish_task(metadata, request, output):
    # DB-only recovery is safe after a trusted completed APPLY result; never
    # re-enter the writer if graph finalization fails or was interrupted.
    try:
        completed = await metadata.complete_isolated_commit_task(request["task_id"], request["id"],
            request["fingerprint"], request["review"]["evaluation_node_id"])
    except Exception:
        return {**output, "task_completed": False,
                "completion_reason": "task_settlement_unconfirmed"}
    return {**output, "task_completed": completed,
            "completion_reason": "completed" if completed else "task_graph_not_ready"}


async def execute_commit_request(runtime, request_id, expected_fingerprint):
    metadata, operational = runtime.storage.metadata, runtime.storage.operational
    request = await operational.get_commit_request(request_id)
    principal = current_cli_principal()
    if (request is None or request["state"] != "approved"
            or request["fingerprint"] != expected_fingerprint
            or fingerprint(request["review"]) != expected_fingerprint
            or request["decision"]["approved"] is not True
            or request["decision"]["by"] != request["creator"]
            or creator_blockers({"user_id": request["creator"], "source": "cli",
                                 "authenticated": True}, principal)):
        raise ValueError("Exact persisted creator decision required")
    node_id = str(uuid5(NAMESPACE_URL, request_id + ":commit-apply"))
    existing = await metadata.get_execution_node(node_id)
    if existing is not None:
        # Trusted completion requires both node ownership settlement and the
        # matching Git transaction, never a report's imported passed flag.
        output = existing.metadata.get("output", {})
        transaction = await metadata.get_git_transaction(output.get("transaction_id", ""))
        if (existing.task_id == request["task_id"] and existing.type == NodeType.APPLY
                and existing.status == NodeStatus.COMPLETED
                and existing.metadata.get("execution_guard", {}).get("state") == "completed"
                and existing.metadata.get("commit_request_fingerprint") == expected_fingerprint
                and transaction is not None and transaction["state"] == "completed"
                and transaction["task_id"] == request["task_id"]
                and transaction["proposal_id"] == request["proposal_id"]
                and transaction["approval_arguments"] == request["review"]["approval_arguments"]
                and transaction["result"] == {key: output.get(key) for key in
                                               ("base_commit", "result_commit")}):
            return await _finish_task(metadata, request, output)
    if datetime.fromisoformat(request["expires_at"]) <= datetime.now(UTC):
        return {"applied": False, "reason": "commit_request_expired"}
    task = await metadata.get_task(request["task_id"])
    if task is None or task.status not in {TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL}:
        return {"applied": False, "reason": "commit_task_not_continuable"}
    saved = request["review"]
    contract = saved["contract"]
    current = await ExperimentCommitService(runtime).review(request["task_id"],
        request["proposal_id"], message=contract["message"], author_name=contract["author_name"],
        author_email=contract["author_email"], evaluation_node_id=saved["evaluation_node_id"],
        routing_profile=saved.get("routing_profile"))
    if fingerprint(current) != expected_fingerprint:
        return {"applied": False, "reason": "commit_review_changed"}
    if not await metadata.activate_commit_task(task.id,
            saved["approval_arguments"]["replay_fingerprint"]):
        return {"applied": False, "reason": "commit_task_changed_before_continuation"}
    node = ExecutionNode(id=node_id, task_id=task.id, type=NodeType.APPLY,
                         metadata={"commit_request_fingerprint": expected_fingerprint})
    owner = str(uuid4())
    if not await metadata.claim_execution_node(node, owner, ""):
        return {"applied": False, "reason": "commit_execution_owned_or_unconfirmed",
                "automatic_retry": False}
    node = await metadata.get_execution_node(node_id)
    try:
        ttl = (datetime.fromisoformat(request["expires_at"]) - datetime.now(UTC)).total_seconds()
        if ttl <= 0:
            raise ValueError("Commit decision expired after claim")
        approval = await runtime.approvals.request("git.commit", saved["approval_arguments"],
            task_id=task.id, requested_by=request["creator"], source="cli", single_use=True,
            ttl_seconds=ttl, resource_scope=request["proposal_id"])
        await runtime.approvals.decide(approval.id, True, decided_by=request["decision"]["by"],
            reason="Persisted explicit commit decision " + request_id, channel="cli")
        result = await _commit_experiment(runtime, task_id=task.id,
            proposal_id=request["proposal_id"], evaluation_node_id=saved["evaluation_node_id"],
            node_id=node_id, owner=owner, approval_id=approval.id, contract=contract)
        if result.get("applied") is not True:
            raise ValueError("Commit not conclusively applied")
        if not await metadata.finish_execution_node(replace(node, status=NodeStatus.COMPLETED,
            metadata={**node.metadata, "output": result}), owner, "completed"):
            raise ValueError("Commit node settlement unconfirmed")
    except BaseException:
        await asyncio.shield(metadata.finish_execution_node(replace(node, status=NodeStatus.WAITING,
            metadata={**node.metadata, "output": {"applied": False,
                "reason": "commit_execution_unconfirmed", "automatic_retry": False}}),
            owner, "uncertain"))
        raise
    return await _finish_task(metadata, request, result)
