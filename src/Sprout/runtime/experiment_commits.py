"""Shared host-side experiment review service; UI-independent, no write dispatch."""

import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.git_identity import GitIdentityRequired, resolve_commit_contract
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.execution.isolated_git_coordinator import _admit, _review_context
from Sprout.gateway.local_identity import creator_blockers, current_cli_principal
from Sprout.orchestration.isolated_worker import COMMIT_ROUTING_PROFILE
from Sprout.orchestration.models import NodeStatus, NodeType


async def decide_commit_request_for_principal(operational, request_id, *, principal,
                                             expected_fingerprint, approved, reason=""):
    """One decision boundary shared by attached Runtime and trusted local CLI."""
    record = await operational.get_commit_request(request_id)
    if record is None:
        raise LookupError("Commit request not found")
    if creator_blockers({"user_id": record["creator"], "source": "cli",
                         "authenticated": True}, principal):
        raise ValueError("Current experiment creator required")
    return await operational.decide_commit_request(request_id, creator=principal.user_id,
        expected_fingerprint=expected_fingerprint, approved=approved, reason=reason)


class ExperimentCommitService:
    """Resolve persisted task/proposal facts, never imported plan files or actor overrides."""

    def __init__(self, runtime):
        self._runtime = runtime

    async def request(self, task_id, proposal_id, **options):
        """Persist a reviewed commit inbox item, not an executable approval."""
        if "routing_profile" in options:
            raise ValueError("Request routing profile is host-owned")
        review = await self.review(task_id, proposal_id, **options)
        if review["status"] != "awaiting_review":
            return review
        if review["proposal_status"] != "approved":
            return {"status": "waiting_code_review", "task_id": task_id,
                    "proposal_id": proposal_id, "write_authorized": False}
        principal = current_cli_principal()
        digest = fingerprint(review)
        identity = str(uuid5(NAMESPACE_URL, "commit-request:" + principal.user_id + ":" + digest))
        operational = self._runtime.storage.operational
        existing = await operational.get_commit_request(identity)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        return await operational.save_commit_request({"id": identity, "task_id": task_id,
            "proposal_id": proposal_id, "creator": principal.user_id, "review": review,
            "fingerprint": digest, "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat()})

    async def decide(self, request_id, *, expected_fingerprint, approved, reason=""):
        """Save operator intent even offline; worker must revalidate before execution."""
        return await decide_commit_request_for_principal(self._runtime.storage.operational,
            request_id, principal=current_cli_principal(),
            expected_fingerprint=expected_fingerprint,
            approved=approved, reason=reason)

    async def refresh(self, request_id, *, expected_fingerprint):
        """Explicit new review generation, not extension of an old approval."""
        runtime = self._runtime
        operational, metadata = runtime.storage.operational, runtime.storage.metadata
        old = await operational.get_commit_request(request_id)
        principal = current_cli_principal()
        if (old is None or old["fingerprint"] != expected_fingerprint
                or creator_blockers({"user_id": old["creator"], "source": "cli",
                                     "authenticated": True}, principal)):
            raise ValueError("Exact creator-bound predecessor required")
        if (old["state"] not in {"pending", "approved"}
                or datetime.fromisoformat(old["expires_at"]) > datetime.now(UTC)
                or await operational.get_commit_dispatch(request_id) is not None):
            raise ValueError("Only expired, never-dispatched requests can refresh")
        node_id = str(uuid5(NAMESPACE_URL, request_id + ":commit-apply"))
        transaction_id = str(uuid5(NAMESPACE_URL, "isolated-commit:" + old["proposal_id"]))
        if (await metadata.get_execution_node(node_id) is not None
                or await metadata.get_git_transaction(transaction_id) is not None):
            raise ValueError("Existing execution requires reconciliation, not refresh")
        saved = old["review"]
        contract = saved["contract"]
        review = await self.review(old["task_id"], old["proposal_id"],
            message=contract["message"], author_name=contract["author_name"],
            author_email=contract["author_email"],
            evaluation_node_id=saved["evaluation_node_id"])
        if review["status"] != "awaiting_review":
            return review
        if review["proposal_status"] != "approved":
            return {"status": "waiting_code_review", "write_authorized": False}
        # One child per predecessor: repeated refresh cannot keep renewing it.
        identity = str(uuid5(NAMESPACE_URL, "commit-refresh:" + request_id))
        now = datetime.now(UTC)
        return await operational.save_commit_request({"id": identity,
            "task_id": old["task_id"], "proposal_id": old["proposal_id"],
            "creator": old["creator"], "review": review, "fingerprint": fingerprint(review),
            "supersedes": request_id, "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat()})

    async def review(self, task_id, proposal_id, *, message, author_name=None,
                     author_email=None, evaluation_node_id=None,
                     routing_profile=COMMIT_ROUTING_PROFILE):
        if routing_profile not in {None, COMMIT_ROUTING_PROFILE}:
            raise ValueError("Unsupported commit routing profile")
        runtime = self._runtime
        task, payload, _, _, plan = await _review_context(
            runtime, task_id, proposal_id, review_only=True)
        try:
            contract = await resolve_commit_contract(task.metadata["replay_plan"], plan,
                message=message, author_name=author_name, author_email=author_email)
        except GitIdentityRequired as exc:
            return {"status": "waiting_configuration", "reason": "git_identity_missing",
                    "missing_fields": list(exc.missing), "task_id": task_id,
                    "proposal_id": proposal_id, "write_authorized": False,
                    "configuration_changed": False, "automatic_retry": False}
        if evaluation_node_id is None:
            nodes = await runtime.storage.metadata.list_execution_nodes(task_id)
            candidates = [node.id for node in nodes if node.type == NodeType.EVALUATION
                and node.status == NodeStatus.COMPLETED
                and node.metadata.get("execution_guard", {}).get("state") == "completed"]
            if len(candidates) != 1:
                return {"status": "waiting_selection", "reason": "evaluation_selection_required",
                        "evaluation_nodes": candidates, "write_authorized": False}
            evaluation_node_id = candidates[0]
        admitted = await _admit(runtime, task_id, proposal_id, evaluation_node_id,
                                contract, review_only=True)
        if admitted[3] != plan:
            raise ValueError("Commit plan changed during review")
        snapshot, arguments = admitted[4:]
        # Display the same persisted snapshot bound to the approval arguments.
        diffs = json.loads(snapshot["diffs_json"])
        result = {"status": "awaiting_review", "task_id": task_id, "proposal_id": proposal_id,
                "proposal_status": snapshot["status"], "target": plan["target"],
                "original": payload["original_root"], "base_commit": plan["base_commit"],
                "files_changed": json.loads(snapshot["files_changed_json"]), "diffs": diffs,
                "evaluation_node_id": evaluation_node_id, "contract": contract,
                "approval_arguments": arguments, "review_fingerprint": fingerprint(arguments),
                "write_authorized": False, "validation_committed": False,
                "configuration_changed": False}
        if routing_profile is not None:
            result["routing_profile"] = routing_profile
        return result
