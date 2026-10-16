"""Durable review inbox and decision outbox in one authority database."""

import json
from datetime import UTC, datetime

from Sprout.storage.contracts.commit_request import decision_matches, validate_request

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiment_commit_requests (
    id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    decision_json TEXT,
    dispatch_state TEXT
);
CREATE TABLE IF NOT EXISTS experiment_commit_dispatches (
    request_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    run_id TEXT
);
"""


async def get_request(db, request_id):
    row = await db.fetchone("SELECT * FROM experiment_commit_requests WHERE id=?", (request_id,))
    if row is None:
        return None
    return {**json.loads(row["record_json"]), "state": row["state"],
            "decision": json.loads(row["decision_json"]) if row["decision_json"] else None,
            "dispatch_state": row["dispatch_state"]}


async def save_request(db, record):
    validate_request(record)
    await db.execute("INSERT OR IGNORE INTO experiment_commit_requests(id,record_json) "
                     "VALUES (?,?)",
                     (record["id"], json.dumps(record, allow_nan=False)))
    saved = await get_request(db, record["id"])
    # An existing ID can never silently overwrite its reviewed content.
    if (saved.get("supersedes") != record.get("supersedes")
            or any(saved[key] != record[key] for key in
                   ("id", "task_id", "proposal_id", "creator", "review", "fingerprint"))):
        raise ValueError("Commit request ID already bound to different content")
    return saved


async def decide_request(db, request_id, *, creator, expected_fingerprint, approved, reason):
    if type(approved) is not bool or not isinstance(reason, str) or len(reason) > 1024:
        raise ValueError("Explicit bounded decision required")
    row = await db.fetchone("SELECT record_json FROM experiment_commit_requests "
                           "WHERE id=? AND state='pending'", (request_id,))
    now = datetime.now(UTC)
    if row is None or not decision_matches(json.loads(row["record_json"]), creator=creator,
                                          expected_fingerprint=expected_fingerprint, now=now):
        return False
    decision = {"approved": approved, "by": creator, "at": now.isoformat(), "reason": reason}
    # Decision and dispatch intent share a single atomic UPDATE. Neither is a
    # consumed Git approval, scheduler submission or authority to execute locally.
    changed = await db.execute("UPDATE experiment_commit_requests SET state=?,decision_json=?,"
        "dispatch_state=? WHERE id=? AND state='pending' AND record_json=?",
        ("approved" if approved else "rejected", json.dumps(decision, allow_nan=False),
         "waiting_schedule" if approved else None, request_id, row["record_json"]))
    return changed == 1


async def get_dispatch(db, request_id):
    row = await db.fetchone("SELECT * FROM experiment_commit_dispatches WHERE request_id=?",
                           (request_id,))
    if row is None:
        return None
    return {**json.loads(row["record_json"]), "state": row["state"], "run_id": row["run_id"]}


async def ensure_dispatch(db, record):
    from Sprout.storage.contracts.commit_request import validate_dispatch

    validate_dispatch(record)
    await db.execute(
        "INSERT OR IGNORE INTO experiment_commit_dispatches(request_id,record_json) "
        "SELECT ?,? WHERE EXISTS (SELECT 1 FROM experiment_commit_requests "
        "WHERE id=? AND state='approved' AND dispatch_state='waiting_schedule' "
        "AND json_extract(record_json,'$.fingerprint')=? "
        "AND json_extract(decision_json,'$.approved')=1 "
        "AND json_extract(decision_json,'$.by')=json_extract(record_json,'$.creator'))",
        (record["request_id"], json.dumps(record, allow_nan=False), record["request_id"],
         record["request_fingerprint"]))
    saved = await get_dispatch(db, record["request_id"])
    if saved is None or any(saved[key] != value for key, value in record.items()):
        raise ValueError("Approved request and immutable dispatch binding required")
    return saved


async def mark_dispatch(db, request_id, state, run_id=None):
    from Sprout.storage.contracts.commit_request import validate_dispatch_state

    validate_dispatch_state(state, run_id)
    await db.execute_batch([
        ("UPDATE experiment_commit_dispatches SET state=?,run_id=? "
         "WHERE request_id=? AND state IN ('pending','uncertain')", (state, run_id, request_id)),
        ("UPDATE experiment_commit_requests SET dispatch_state=? "
         "WHERE id=? AND state='approved' AND changes()=1", (state, request_id)),
    ])
    saved = await get_dispatch(db, request_id)
    return saved is not None and saved["state"] == state and saved["run_id"] == run_id
