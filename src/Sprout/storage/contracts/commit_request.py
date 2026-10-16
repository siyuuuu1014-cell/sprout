"""Immutable operator review requests, not executable Git grants."""

import hashlib
from datetime import datetime

from Sprout.execution.git_transaction_contract import fingerprint


def validate_request(record):
    required = {"id", "task_id", "proposal_id", "creator", "review", "fingerprint",
                "created_at", "expires_at"}
    if set(record) - {"supersedes"} != required:
        raise ValueError("Exact immutable commit request required")
    if "supersedes" in record and (not isinstance(record["supersedes"], str)
            or not record["supersedes"] or record["supersedes"] == record["id"]):
        raise ValueError("Explicit predecessor request required")
    review = record["review"]
    if (not all(isinstance(record[key], str) and record[key] for key in
                ("id", "task_id", "proposal_id", "creator"))
            or not isinstance(review, dict) or review.get("proposal_status") != "approved"
            or review.get("write_authorized") is not False
            or review.get("task_id") != record["task_id"]
            or review.get("proposal_id") != record["proposal_id"]
            or record["fingerprint"] != fingerprint(review)):
        raise ValueError("Bound approved-proposal review required")
    created = datetime.fromisoformat(record["created_at"])
    expires = datetime.fromisoformat(record["expires_at"])
    if created.tzinfo is None or expires.tzinfo is None or expires <= created:
        raise ValueError("Bounded request lifetime required")


def decision_matches(record, *, creator, expected_fingerprint, now):
    return (record["creator"] == creator and record["fingerprint"] == expected_fingerprint
            and datetime.fromisoformat(record["expires_at"]) > now)


def validate_dispatch(record):
    fields = {"request_id", "request_fingerprint", "workflow_id", "host", "namespace", "task_queue"}
    if (not isinstance(record, dict) or set(record) != fields
            or not all(isinstance(value, str) and value for value in record.values())):
        raise ValueError("Exact durable dispatch binding required")
    if record["workflow_id"] != commit_workflow_id(record["request_id"],
                                                   record["request_fingerprint"]):
        raise ValueError("Stable request-bound workflow identity required")


def commit_workflow_id(request_id, request_fingerprint):
    digest = hashlib.sha256((request_id + ":" + request_fingerprint).encode()).hexdigest()
    return "sprout-commit-" + digest


def validate_dispatch_state(state, run_id):
    if state not in {"pending", "uncertain", "accepted"}:
        raise ValueError("Scheduler acknowledgement only; not execution completion")
    if state == "accepted" and (not isinstance(run_id, str) or not run_id):
        raise ValueError("Observed Temporal run ID required")
    if state != "accepted" and run_id is not None:
        raise ValueError("Unconfirmed dispatch cannot claim a run ID")
