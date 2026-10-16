"""Non-expiring experiment Git claims; not an approval or filesystem authority."""

import json
import re
from datetime import UTC, datetime

from Sprout.execution.git_transaction_contract import fingerprint

SCHEMA = """
CREATE TABLE IF NOT EXISTS isolated_git_transactions (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    state TEXT NOT NULL,
    record_json TEXT NOT NULL,
    result_json TEXT,
    operation TEXT NOT NULL,
    UNIQUE(proposal_id, operation)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_git_transaction_workspace_owner
ON isolated_git_transactions(workspace_id) WHERE state IN ('active', 'uncertain');
CREATE UNIQUE INDEX IF NOT EXISTS idx_git_transaction_target_owner
ON isolated_git_transactions(json_extract(record_json, '$.target') COLLATE NOCASE)
WHERE state IN ('active', 'uncertain');
"""


async def proposal_snapshot(db, proposal_id):
    row = await db.fetchone("SELECT * FROM change_proposals WHERE id=?", (proposal_id,))
    return dict(row) if row is not None else None


async def get_transaction(db, transaction_id):
    row = await db.fetchone("SELECT * FROM isolated_git_transactions WHERE id=?", (transaction_id,))
    if row is None:
        return None
    return {**json.loads(row["record_json"]), "state": row["state"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None}


async def claim_transaction(db, record, snapshot):
    """Caller must verify actual approval, immutable plan and measurement provenance.

    This SQL only atomically binds the exact persisted proposal and live node,
    preventing stale snapshots and concurrent writes. approval_id is an audit
    reference, never interpreted here as proof of approval.
    """
    if any(key in record for key in ("state", "result", "proposal_snapshot", "write_started")):
        raise ValueError("Transaction lifecycle fields are storage-owned")
    operation = record.get("operation")
    if operation not in {"commit", "rollback"}:
        raise ValueError("Explicit experiment Git operation required")
    if not all(isinstance(record.get(key), str) and record[key] for key in
               ("id", "proposal_id", "workspace_id", "task_id", "node_id", "owner",
                "approval_id", "target", "replay_fingerprint")):
        raise ValueError("Bound Git transaction identities required")
    plan = record.get("plan")
    if (not isinstance(plan, dict) or plan.get("operation") != operation
            or plan.get("write_authorized") is not False or plan.get("target") != record["target"]
            or record.get("plan_fingerprint") != fingerprint(plan)
            or record.get("proposal_fingerprint") != fingerprint(snapshot)):
        raise ValueError("Exact read-only plan and proposal fingerprints required")
    if not snapshot or snapshot.get("id") != record["proposal_id"]:
        raise ValueError("Persisted proposal snapshot required")
    commit_keys = ("base_commit", "applied_commit") if operation == "rollback" else ("base_commit",)
    if not all(isinstance(plan.get(key), str) and re.fullmatch(
            r"[0-9a-f]{40}|[0-9a-f]{64}", plan[key]) for key in commit_keys):
        raise ValueError("Frozen full commit identities required")
    # Fixed database column inventory, never identifiers supplied by a caller.
    columns = ("id", "task_id", "sandbox_json", "files_changed_json", "test_results_json",
               "diffs_json", "risk", "required_capability_diff_json", "rollback_plan",
               "status", "metadata_json", "created_at")
    if set(snapshot) != set(columns):
        raise ValueError("Complete proposal snapshot required")
    expected_status = "approved" if operation == "commit" else "applied"
    changed = await db.execute(
        "INSERT OR IGNORE INTO isolated_git_transactions "
        "(id,proposal_id,workspace_id,owner,state,record_json,operation) "
        "SELECT ?,?,?,?,'active',?,? WHERE EXISTS ("
        "SELECT 1 FROM change_proposals p JOIN tasks t ON t.id=p.task_id "
        "JOIN execution_nodes n ON n.task_id=t.id JOIN workspaces w ON w.id=t.workspace_id "
        "WHERE " + " AND ".join("p." + column + " IS ?" for column in columns) +
        " AND p.status=? AND t.id=? AND t.workspace_id=? AND w.root=? "
        "AND t.status='running' AND n.id=? AND n.status='running' AND n.type='apply' "
        "AND json_extract(n.metadata_json,'$.execution_guard.owner')=? "
        "AND json_extract(n.metadata_json,'$.execution_guard.state')='active' "
        "AND json_extract(t.metadata_json,'$.execution_mode')='code' "
        "AND json_extract(t.metadata_json,'$.replay_plan.payload.schema_version')=5 "
        "AND json_extract(t.metadata_json,'$.replay_plan.fingerprint')=?)",
        (record["id"], record["proposal_id"], record["workspace_id"], record["owner"],
         json.dumps({**record, "proposal_snapshot": snapshot}, allow_nan=False), operation,
         *(snapshot[column] for column in columns), expected_status, record["task_id"],
         record["workspace_id"], record["target"], record["node_id"], record["owner"],
         record["replay_fingerprint"]))
    return changed == 1


async def begin_commit_write(db, transaction_id, owner):
    """One irreversible write-start marker, not evidence or approval authority.

    Persist before spawning Git. Crashes before/after spawn both retain the
    marker; no lease expiry, reset, owner takeover or automatic retry.
    """
    row = await db.fetchone("SELECT record_json FROM isolated_git_transactions "
                           "WHERE id=? AND owner=? AND state='active' AND operation='commit'",
                           (transaction_id, owner))
    if row is None:
        return False
    raw = row["record_json"]
    record = json.loads(raw)
    if "write_started" in record:
        return False
    snapshot = record["proposal_snapshot"]
    columns = ("id", "task_id", "sandbox_json", "files_changed_json", "test_results_json",
               "diffs_json", "risk", "required_capability_diff_json", "rollback_plan",
               "status", "metadata_json", "created_at")
    if set(snapshot) != set(columns) or snapshot["status"] != "approved":
        return False
    record["write_started"] = {"owner": owner, "at": datetime.now(UTC).isoformat()}
    changed = await db.execute(
        "UPDATE isolated_git_transactions SET record_json=? "
        "WHERE id=? AND owner=? AND state='active' AND operation='commit' AND record_json=? "
        "AND EXISTS (SELECT 1 FROM change_proposals p JOIN tasks t ON t.id=p.task_id "
        "JOIN workspaces w ON w.id=t.workspace_id JOIN execution_nodes n ON n.task_id=t.id WHERE "
        + " AND ".join("p." + column + " IS ?" for column in columns)
        + " AND t.id=? AND t.workspace_id=? AND w.root=? AND t.status='running' "
          "AND n.id=? AND n.type='apply' AND n.status='running' "
          "AND json_extract(n.metadata_json,'$.execution_guard.owner')=? "
          "AND json_extract(n.metadata_json,'$.execution_guard.state')='active' "
          "AND json_extract(t.metadata_json,'$.execution_mode')='code' "
          "AND json_extract(t.metadata_json,'$.replay_plan.payload.schema_version')=5 "
          "AND json_extract(t.metadata_json,'$.replay_plan.fingerprint')=?)",
        (json.dumps(record, allow_nan=False), transaction_id, owner, raw,
         *(snapshot[column] for column in columns), record["task_id"], record["workspace_id"],
         record["target"], record["node_id"], owner, record["replay_fingerprint"]))
    return changed == 1


async def finish_transaction(db, transaction_id, owner, state, result):
    if state not in {"completed", "uncertain"}:
        raise ValueError("Git settlement must be completed or uncertain; no reset or release")
    if state == "completed":
        if not all(isinstance(result.get(key), str) and re.fullmatch(
                r"[0-9a-f]{40}|[0-9a-f]{64}", result[key])
                for key in ("base_commit", "result_commit")):
            raise ValueError("Full observed commit identities required")
        current = await get_transaction(db, transaction_id)
        if current is None:
            return False
        plan = current["plan"]
        expected = (plan["base_commit"] if current["operation"] == "commit"
                    else plan["applied_commit"])
        if result["base_commit"] != expected or result["result_commit"] == expected:
            raise ValueError("Settlement must match the frozen base and a new commit")
    changed = await db.execute(
        "UPDATE isolated_git_transactions SET state=?, result_json=? "
        "WHERE id=? AND owner=? AND state='active'",
        (state, json.dumps(result, allow_nan=False), transaction_id, owner))
    return changed == 1


async def settle_commit_proposal(db, transaction_id, owner, result):
    """Internal coordinator settlement, never Git verification or authorization.

    Atomically CAS the frozen proposal and live owner to APPLIED together with
    transaction completion. The caller must independently verify the observed
    Git commit/tree; imported commit hashes cannot establish that evidence.
    """
    current = await get_transaction(db, transaction_id)
    if current is None or current["state"] != "active" or current["owner"] != owner:
        return False
    if current["operation"] != "commit":
        raise ValueError("Commit settlement only; rollback requires its own coordinator")
    if (not isinstance(result, dict) or any(not isinstance(result.get(key), str)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", result[key])
            for key in ("base_commit", "result_commit"))
            or result["base_commit"] != current["plan"]["base_commit"]
            or result["result_commit"] == result["base_commit"]):
        raise ValueError("New observed commit must match the frozen base")
    snapshot = current["proposal_snapshot"]
    metadata = json.loads(snapshot["metadata_json"])
    metadata.update(base_commit=result["base_commit"], applied_commit=result["result_commit"],
                    isolated_git_transaction_id=transaction_id)
    columns = tuple(snapshot)  # Identifiers are constrained to the fixed claim inventory below.
    expected = {"id", "task_id", "sandbox_json", "files_changed_json", "test_results_json",
                "diffs_json", "risk", "required_capability_diff_json", "rollback_plan",
                "status", "metadata_json", "created_at"}
    if set(columns) != expected or snapshot["status"] != "approved":
        raise ValueError("Complete approved proposal snapshot required")
    encoded_result = json.dumps(result, allow_nan=False)
    await db.execute_batch([
        ("UPDATE change_proposals AS p SET status='applied', metadata_json=? WHERE "
         + " AND ".join("p." + column + " IS ?" for column in columns)
         + " AND EXISTS (SELECT 1 FROM isolated_git_transactions g JOIN tasks t ON t.id=p.task_id "
           "JOIN workspaces w ON w.id=t.workspace_id JOIN execution_nodes n ON n.task_id=t.id "
           "WHERE g.id=? AND g.owner=? AND g.state='active' AND g.operation='commit' "
           "AND t.id=? AND t.workspace_id=? AND w.root=? AND t.status='running' "
           "AND n.id=? AND n.type='apply' AND n.status='running' "
           "AND json_extract(n.metadata_json,'$.execution_guard.owner')=? "
           "AND json_extract(n.metadata_json,'$.execution_guard.state')='active' "
           "AND json_extract(t.metadata_json,'$.execution_mode')='code' "
           "AND json_extract(t.metadata_json,'$.replay_plan.payload.schema_version')=5 "
           "AND json_extract(t.metadata_json,'$.replay_plan.fingerprint')=?)",
         (json.dumps(metadata, allow_nan=False), *(snapshot[column] for column in columns),
          transaction_id, owner, current["task_id"], current["workspace_id"], current["target"],
          current["node_id"], owner, current["replay_fingerprint"])),
        # Both statements share BEGIN IMMEDIATE. The second can settle only
        # when the exact proposal CAS immediately above changed one row.
        ("UPDATE isolated_git_transactions SET state='completed',result_json=? "
         "WHERE id=? AND owner=? AND state='active' AND changes()=1",
         (encoded_result, transaction_id, owner)),
    ])
    settled = await get_transaction(db, transaction_id)
    return settled["state"] == "completed" and settled["result"] == result
