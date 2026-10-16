"""Explicit continuation of an existing tool/process approval wait."""

from dataclasses import asdict

from Sprout.cli.commit_submission import _route
from Sprout.cli.experiment_runtime import experiment_runtime
from Sprout.evolution.coding_assembly import build_coding_session_factory
from Sprout.evolution.coding_run import coding_execution
from Sprout.orchestration.models import NodeStatus, NodeType
from Sprout.security.approval import ApprovalStatus
from Sprout.storage.local.sqlite.commit_status import _regular


async def continue_coding(database, task_id, *, settings, principal, approval_id=None,
                          source_metadata=None):
    """Never create a task, choose a new route, approve code, or release unknown owners."""
    database = _regular(database)
    host, namespace, _ = _route(database, task_id, principal)
    async with experiment_runtime(
        database, task_id, settings=settings, source_metadata=source_metadata,
    ) as (runtime, task):
        if task.status != "waiting_approval":
            raise ValueError("Only an existing approval wait can continue here")
        nodes = await runtime.storage.metadata.list_execution_nodes(task_id)
        waiting = [node for node in nodes if node.status is NodeStatus.WAITING]
        if len(waiting) != 1:
            raise ValueError("One persisted waiting node required")
        node = waiting[0]
        base = {"task_id": task_id, "status": "waiting_approval",
                "validation_committed": False, "git_executed": False}
        if node.type in {NodeType.APPROVAL, NodeType.APPLY}:
            if approval_id is not None:
                raise ValueError("Code approval/application requires independent commit workflow")
            return {**base, "next_action": "Review proposal; use independent commit-* workflow",
                    "proposal_id": node.metadata.get("output", {}).get("proposal_id")}
        if node.type not in {NodeType.TEST_DESIGN, NodeType.AGENT, NodeType.EVALUATION}:
            raise ValueError("Unsupported isolated coding approval wait")
        build_coding_session_factory(runtime, task)
        approval_ids = set(node.metadata.get("output", {}).get("pending_approvals") or ())
        if not approval_ids:
            raise ValueError("Waiting node has no persisted approval references")
        pending = [grant for grant in await runtime.pending_approvals()
                   if grant.task_id == task_id and grant.id in approval_ids]
        if approval_id is not None:
            grant = await runtime.approvals.store.get_approval(approval_id)
            if (approval_id not in approval_ids or grant is None
                    or grant.task_id != task_id or grant.source != "cli"
                    or grant.requested_by != principal.user_id
                    or grant.resource_scope != task.workspace_id
                    or grant.status is not ApprovalStatus.PENDING
                    or grant.tool not in {"process_run", "isolated_coding_command", "skill_view"}):
                raise ValueError("Exact current-task pending tool/process approval required")
            decision = await runtime.approvals.decide(approval_id, True,
                decided_by=principal.user_id, channel="cli",
                reason="Explicit replay-continue --approve; not code or Git approval")
            if decision.status is not ApprovalStatus.APPROVED:
                return {**base, "reason": "approval_not_granted",
                        "next_action": "Review expiry/state"}
            pending = [item for item in pending if item.id != approval_id]
        if pending:
            return {**base, "next_action": "Review requests, then use --approve <exact-id>",
                    "pending_approvals": [{"approval_id": grant.id, "tool": grant.tool,
                        "action_summary": grant.action_summary,
                        "expires_at": grant.expires_at.isoformat() if grant.expires_at else None}
                        for grant in pending]}
        for identity in approval_ids:
            grant = await runtime.approvals.store.get_approval(identity)
            if grant is None or grant.status not in {
                ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED,
            }:
                return {**base, "reason": "approval_not_granted",
                        "next_action": "Review the current node approval state"}
        async with coding_execution(
            runtime, task, host=host, namespace=namespace, resume=True,
        ) as run:
            result = await run()
            return {**asdict(result), "task_id": task_id,
                    "validation_committed": False, "git_executed": False,
                    "measurement_status": "not_measured"}
