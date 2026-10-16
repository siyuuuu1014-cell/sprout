"""Read-only task-scoped inbox/status projection; never execution authority."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.gateway.local_identity import creator_blockers
from Sprout.security.redact import Redactor


def _regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or str(path.resolve()).startswith("\\\\"):
        raise ValueError("Existing regular local database required")
    return path.resolve(strict=True)


def _status(connection, request, dispatch):
    node_id = str(uuid5(NAMESPACE_URL, request["id"] + ":commit-apply"))
    node = connection.execute("SELECT task_id,type,status,metadata_json FROM execution_nodes "
                              "WHERE id=?", (node_id,)).fetchone()
    if node is not None:
        metadata = json.loads(node["metadata_json"])
        if (node["task_id"] != request["task_id"] or node["type"] != "apply"
                or metadata.get("commit_request_fingerprint") != request["fingerprint"]):
            return "needs_reconciliation", None
        guard = metadata.get("execution_guard", {})
        output = metadata.get("output", {})
        if guard.get("state") == "uncertain":
            return "needs_reconciliation", None
        if guard.get("state") == "active":
            return "executing_or_unconfirmed", None
        transaction = connection.execute("SELECT state,record_json,result_json "
            "FROM isolated_git_transactions WHERE id=?", (output.get("transaction_id", ""),)
        ).fetchone()
        if transaction is not None:
            bound = json.loads(transaction["record_json"])
            result = json.loads(transaction["result_json"] or "null")
            if (node["task_id"] == request["task_id"] and node["type"] == "apply"
                    and node["status"] == "completed" and guard.get("state") == "completed"
                    and metadata.get("commit_request_fingerprint") == request["fingerprint"]
                    and output.get("applied") is True and transaction["state"] == "completed"
                    and bound["task_id"] == request["task_id"]
                    and bound["proposal_id"] == request["proposal_id"]
                    and bound["approval_arguments"] == request["review"]["approval_arguments"]
                    and result == {key: output.get(key) for key in
                                   ("base_commit", "result_commit")}):
                return "applied", result["result_commit"]
        return "needs_reconciliation", None
    if request["state"] == "rejected":
        return "rejected", None
    if datetime.fromisoformat(request["expires_at"]) <= datetime.now(UTC):
        return "expired", None
    if request["state"] == "pending":
        return "awaiting_confirmation", None
    if dispatch is not None and dispatch["state"] == "uncertain":
        return "delivery_unconfirmed", None
    if dispatch is not None and dispatch["state"] == "accepted":
        return "accepted_waiting_execution", None
    return "waiting_schedule", None


def commit_status_report(database, task_id, *, principal, request_id=None, include_review=False):
    """Open existing metadata and sibling operational DBs in SQLite read-only mode.

    Status is an observation, not a grant, global atomic snapshot or permission
    to retry. Active/uncertain ownership is never cleared by inspection.
    """
    database = _regular(database)
    operational = _regular(database.parent / "operational.db")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("ATTACH DATABASE ? AS operations",
                           (operational.as_uri() + "?mode=ro",))
        connection.execute("BEGIN")
        task = connection.execute("SELECT actor_json,source,status,metadata_json FROM tasks "
                                  "WHERE id=?",
                                  (task_id,)).fetchone()
        if task is None:
            raise ValueError("Task not found")
        actor = json.loads(task["actor_json"])
        creator = {key: actor.get(key) for key in ("user_id", "source", "authenticated")}
        if task["source"] != "cli" or creator_blockers(creator, principal):
            raise ValueError("Current CLI task creator required")
        rows = connection.execute("SELECT * FROM operations.experiment_commit_requests "
            "WHERE json_extract(record_json,'$.task_id')=? "
            "AND json_extract(record_json,'$.creator')=? "
            "AND (? IS NULL OR id=?) ORDER BY id LIMIT 201",
            (task_id, principal.user_id, request_id, request_id)).fetchall()
        items = []
        task_completion = json.loads(task["metadata_json"]).get("isolated_commit_completion", {})
        for row in rows[:200]:
            if len(row["record_json"]) > 2 * 1024 * 1024:
                raise ValueError("Commit request exceeds inspection limit")
            request = json.loads(row["record_json"])
            if fingerprint(request["review"]) != request["fingerprint"]:
                raise ValueError("Commit review fingerprint mismatch")
            request["state"] = row["state"]
            dispatch = connection.execute("SELECT state,record_json,run_id "
                "FROM operations.experiment_commit_dispatches WHERE request_id=?",
                (request["id"],)).fetchone()
            if dispatch is not None and json.loads(dispatch["record_json"])[
                    "request_fingerprint"] != request["fingerprint"]:
                raise ValueError("Commit dispatch binding mismatch")
            status, commit = _status(connection, request, dispatch)
            items.append({"request_id": request["id"], "proposal_id": request["proposal_id"],
                "task_completed": task["status"] == "completed"
                and task_completion.get("request_id") == request["id"]
                and task_completion.get("request_fingerprint") == request["fingerprint"],
                "status": status, "decision_state": row["state"],
                "expires_at": request["expires_at"],
                "dispatch_state": dispatch["state"] if dispatch else row["dispatch_state"],
                "run_id": dispatch["run_id"] if dispatch else None,
                "result_commit": commit, "validation_committed": False})
            if include_review:
                raw = json.dumps(request["review"], ensure_ascii=False)
                masked = Redactor().scrub(raw)
                items[-1].update(decision_fingerprint=request["fingerprint"],
                    review=json.loads(masked), display_redacted=masked != raw)
            if "supersedes" in request:
                items[-1]["supersedes"] = request["supersedes"]
        if request_id is not None and not items:
            raise ValueError("Commit request not found for current task creator")
        return {"task_id": task_id, "task_status": task["status"], "requests": items,
                "truncated": len(rows) > 200, "read_only": True, "write_authorized": False,
                "automatic_retry": False}
    except (sqlite3.Error, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Commit status evidence unavailable or malformed") from exc
    finally:
        connection.close()
