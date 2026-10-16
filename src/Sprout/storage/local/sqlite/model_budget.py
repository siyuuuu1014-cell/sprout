"""Task-wide provider-attempt reservations in metadata, not best-effort usage."""

import json
from uuid import uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_model_requests (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, node_id TEXT NOT NULL,
    call_limit INTEGER NOT NULL, state TEXT NOT NULL, usage_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_model_requests_task ON task_model_requests(task_id);
"""


async def reserve(db, task_id, node_id, limit):
    if type(limit) is not int or limit <= 0:
        raise ValueError("Positive explicit task request limit required")
    identity = str(uuid4())
    changed = await db.execute("INSERT INTO task_model_requests "
        "SELECT ?,?,?,?,'reserved',NULL FROM tasks WHERE id=? "
        "AND json_extract(budget_json,'$.max_model_calls')=? "
        "AND (SELECT COUNT(*) FROM task_model_requests WHERE task_id=?)<? "
        "AND NOT EXISTS(SELECT 1 FROM task_model_requests WHERE task_id=? AND call_limit!=?)",
        (identity, task_id, node_id, limit, task_id, limit, task_id, limit, task_id, limit))
    return identity if changed == 1 else None


async def finish(db, request_id, state, usage):
    if state not in {"responded", "unconfirmed"}:
        raise ValueError("Invalid model request settlement")
    changed = await db.execute("UPDATE task_model_requests SET state=?,usage_json=? "
        "WHERE id=? AND state='reserved'", (state, json.dumps(usage), request_id))
    return changed == 1


async def summary(db, task_id):
    row = await db.fetchone("SELECT COUNT(*) AS consumed, "
        "MIN(call_limit) AS call_limit, "
        "SUM(CASE WHEN state='responded' THEN 1 ELSE 0 END) AS responded "
        "FROM task_model_requests WHERE task_id=?", (task_id,))
    return {"consumed": row["consumed"], "call_limit": row["call_limit"],
            "responded": row["responded"] or 0,
            "unknown_or_failed": row["consumed"] - (row["responded"] or 0)}


async def report(db, task_id, configured):
    from Sprout.task.reporting import budget_projection

    exists = await db.fetchone("SELECT 1 FROM sqlite_master WHERE type='table' "
                               "AND name='task_model_requests'")
    rows = None
    if exists:
        records = await db.fetchall("SELECT call_limit,state,usage_json FROM task_model_requests "
                                    "WHERE task_id=?", (task_id,))
        rows = [(row["call_limit"], row["state"], row["usage_json"]) for row in records]
    return budget_projection(rows, configured)
