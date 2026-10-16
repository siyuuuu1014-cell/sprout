"""Read-only coding projection; decisions and execution remain in their owners."""

import json
import sqlite3
from datetime import UTC, datetime

from Sprout.security.redact import Redactor
from Sprout.storage.local.sqlite.commit_status import _regular, commit_status_report
from Sprout.task.reporting import budget_projection, task_stage


def _label(value):
    return Redactor().scrub(str(value))[:256] if value is not None else None


def _budget(connection, task_id, configured):
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='task_model_requests'").fetchone()
    if not exists:
        return budget_projection(None, configured)
    rows = connection.execute("SELECT call_limit,state,usage_json FROM task_model_requests "
                              "WHERE task_id=?", (task_id,)).fetchall()
    return budget_projection(rows, configured)


def coding_report(database, task_id, *, principal):
    database = _regular(database)
    # Reuse creator checks and established commit evidence interpretation.
    commits = commit_status_report(database, task_id, principal=principal)
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        task = connection.execute("SELECT status,budget_json FROM tasks WHERE id=?",
                                  (task_id,)).fetchone()
        nodes = []
        verification = {"status": "not_observed", "reported_verified": False}
        for row in connection.execute("SELECT id,type,status,attempts,metadata_json "
            "FROM execution_nodes WHERE task_id=? ORDER BY created_at,id", (task_id,)):
            metadata = json.loads(row["metadata_json"])
            output = metadata.get("output", {})
            output = output if isinstance(output, dict) else {}
            node = {key: row[key] for key in ("id", "type", "status", "attempts")}
            node.update(ownership=metadata.get("execution_guard", {}).get("state"),
                        waiting_kind=_label(output.get("waiting_kind")),
                        reason=_label(output.get("reason")),
                        budget_stopped=any(kind in str(metadata.get("error") or "")
                            for kind in ("ModelBudgetExhausted", "LiveRequestLimit")))
            nodes.append(node)
            report = output.get("verification_report")
            if row["type"] == "evaluation" and isinstance(report, dict):
                checks = report.get("checks", [])
                verification = {"node_id": row["id"], "status": report.get("status"),
                    "reported_verified": report.get("verified") is True
                        and row["status"] == "completed" and node["ownership"] == "completed",
                    "check_count": len(checks),
                    "failed_checks": [_label(item.get("name"))
                                      for item in checks if not item.get("passed")]}
        configured = json.loads(task["budget_json"]).get("max_model_calls", 0)
        budget = _budget(connection, task_id, configured)
        submissions = []
        for row in connection.execute("SELECT id,record_json FROM task_submissions WHERE task_id=? "
                                      "ORDER BY rowid", (task_id,)):
            record = json.loads(row["record_json"])
            submissions.append({"id": row["id"], "state": record["state"],
                "accepted": record.get("accepted", False),
                "historical_result_status": (record.get("result") or {}).get("status")})
        status = task["status"]
    finally:
        connection.close()
    stage, action = task_stage(status, nodes, submissions)
    return {"task_id": task_id, "task_status": status, "stage": stage, "next_action": action,
            "budget": budget, "verification": verification, "nodes": nodes,
            "submissions": submissions, "commit_requests": commits["requests"],
            "observed_at": datetime.now(UTC).isoformat(),
            "snapshot_scope": "separate metadata/operational observations; not globally atomic",
            "read_only": True, "execution_authority": False, "automatic_retry": False}
