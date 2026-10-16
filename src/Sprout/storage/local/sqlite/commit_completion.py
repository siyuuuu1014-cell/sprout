"""Join a completed experiment commit back to its persisted original graph.

This is database-only settlement. The original Temporal result remains historical;
the task records which later commit completed its approval/apply obligations.
"""

import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.models import TestResult
from Sprout.execution.verification import verification_blocker


def _snapshot(table, row):
    # Tables/columns are from local fixed-schema rows, never caller identifiers.
    return ("EXISTS (SELECT 1 FROM " + table + " WHERE " +
            " AND ".join(key + " IS ?" for key in row.keys()) + ")", tuple(row))


def _completed_node(raw, output, completion, previous=None, *, skipped=False):
    metadata = json.loads(previous["metadata_json"]) if previous else dict(raw.get("metadata", {}))
    metadata.update(output=output, commit_completion=completion)
    if not skipped:
        metadata["execution_guard"] = {**metadata.get("execution_guard", {}),
            "state": "completed", "settled_at": completion["at"],
            "settled_by_commit_node": completion["commit_node_id"]}
    return (raw["id"], raw["task_id"], raw["type"], json.dumps(raw["dependencies"]),
            "skipped" if skipped else "completed", previous["attempts"] if previous else 0,
            json.dumps(raw["budget"]), previous["result_ref_json"] if previous else None,
            json.dumps(metadata, allow_nan=False), raw["created_at"])


async def complete_commit_task(db, task_id, request_id, request_fingerprint, evaluation_id):
    task = await db.fetchone("SELECT * FROM tasks WHERE id=?", (task_id,))
    if task is None:
        return False
    task_metadata = json.loads(task["metadata_json"])
    previous = task_metadata.get("isolated_commit_completion", {})
    if task["status"] == "completed":
        return (previous.get("request_id") == request_id
                and previous.get("request_fingerprint") == request_fingerprint)
    if (task["status"] != "running" or task_metadata.get("execution_mode") != "code"
            or task_metadata.get("replay_plan", {}).get("payload", {}).get("schema_version") != 5):
        return False
    rows = await db.fetchall("SELECT * FROM execution_nodes WHERE task_id=?", (task_id,))
    nodes = {row["id"]: row for row in rows}
    commit_id = str(uuid5(NAMESPACE_URL, request_id + ":commit-apply"))
    commit, evaluation = nodes.get(commit_id), nodes.get(evaluation_id)
    if commit is None or evaluation is None:
        return False
    cm, em = json.loads(commit["metadata_json"]), json.loads(evaluation["metadata_json"])
    output = cm.get("output", {})
    if (commit["type"] != "apply" or commit["status"] != "completed"
            or cm.get("execution_guard", {}).get("state") != "completed"
            or cm.get("commit_request_fingerprint") != request_fingerprint
            or output.get("applied") is not True or evaluation["type"] != "evaluation"
            or evaluation["status"] != "completed"
            or em.get("execution_guard", {}).get("state") != "completed"
            or em.get("output", {}).get("status") != "passed"):
        return False
    transaction = await db.fetchone("SELECT * FROM isolated_git_transactions WHERE id=?",
                                   (output.get("transaction_id", ""),))
    if transaction is None or transaction["state"] != "completed":
        return False
    tx = json.loads(transaction["record_json"])
    if (tx["task_id"] != task_id or tx["node_id"] != commit_id
            or tx["replay_fingerprint"] != task_metadata["replay_plan"]["fingerprint"]
            or json.loads(transaction["result_json"]) != {key: output.get(key) for key in
                                                         ("base_commit", "result_commit")}):
        return False
    proposal = await db.fetchone("SELECT * FROM change_proposals WHERE id=?",
                                (tx["proposal_id"],))
    if proposal is None or proposal["task_id"] != task_id or proposal["status"] != "applied":
        return False
    pm = json.loads(proposal["metadata_json"])
    verification = pm.get("verification", {})
    if (pm.get("verification_override") or pm.get("applied_commit") != output["result_commit"]
            or verification_blocker([TestResult(**item) for item in
                json.loads(proposal["test_results_json"])], verification)):
        return False
    submissions = await db.fetchall("SELECT * FROM task_submissions WHERE task_id=?", (task_id,))
    candidates = []
    for row in submissions:
        record = json.loads(row["record_json"])
        if (record.get("accepted") is True and record.get("state") == "resolved"
                and record.get("result", {}).get("status") == "waiting_approval"
                and record.get("result", {}).get("node_id") == task_id + ":approval"):
            candidates.append((row, record))
    if len(candidates) != 1:
        return False
    source, record = candidates[0]
    graph = record["payload"].get("nodes", [])
    approval_id, apply_id = task_id + ":approval", task_id + ":apply"
    if (len(graph) < 3 or [n["id"] for n in graph[-2:]] != [approval_id, apply_id]
            or [n["type"] for n in graph[-2:]] != ["approval", "apply"]
            or len({n["id"] for n in graph}) != len(graph)
            or any(n["task_id"] != task_id for n in graph)):
        return False
    graph_ids = {node["id"] for node in graph}
    if any(row["id"] not in graph_ids | {commit_id}
           and row["status"] not in {"completed", "skipped"}
           for row in rows):
        return False
    completion = {"schema_version": 1, "request_id": request_id,
        "request_fingerprint": request_fingerprint, "commit_node_id": commit_id,
        "evaluation_node_id": evaluation_id, "transaction_id": tx["id"],
        "result_commit": output["result_commit"], "source_workflow_id": record["id"],
        "at": datetime.now(UTC).isoformat()}
    updates, seen_evaluation = [], False
    for raw in graph[:-2]:
        current = nodes.get(raw["id"])
        if current is not None:
            guard = json.loads(current["metadata_json"]).get("execution_guard", {})
            if (current["type"] != raw["type"] or current["status"] not in {"completed", "skipped"}
                    or (current["status"] == "completed" and guard.get("state") != "completed")):
                return False
        elif seen_evaluation and raw["type"] in {"agent", "evaluation"}:
            updates.append(_completed_node(raw, {"skipped": True,
                "reason": "earlier_evaluation_passed", "evaluation_node_id": evaluation_id},
                completion, skipped=True))
        else:
            return False
        seen_evaluation = seen_evaluation or raw["id"] == evaluation_id
    if not seen_evaluation:
        return False
    for raw in graph[-2:]:
        current = nodes.get(raw["id"])
        if current is not None:
            guard = json.loads(current["metadata_json"]).get("execution_guard", {})
            if (current["type"] != raw["type"] or current["status"] != "waiting"
                    or guard.get("state") != "waiting"):
                return False
            if (raw["type"] == "approval" and json.loads(current["metadata_json"]).get(
                    "output", {}).get("proposal_id") != tx["proposal_id"]):
                return False
        elif raw["type"] == "approval":
            return False
        updates.append(_completed_node(raw, {**output, "proposal_id": tx["proposal_id"],
            "waiting": False, "settled_by_commit_node": commit_id}, completion, current))
    gates, parameters = [], []
    for table, row in [("tasks", task), ("isolated_git_transactions", transaction),
                       ("change_proposals", proposal), ("task_submissions", source),
                       *(("execution_nodes", row) for row in rows)]:
        sql, values = _snapshot(table, row)
        gates.append(sql)
        parameters.extend(values)
    task_metadata["isolated_commit_completion"] = completion
    statements = [("UPDATE tasks SET status='completed',metadata_json=? WHERE id=? AND " +
        " AND ".join(gates) + " AND (SELECT COUNT(*) FROM execution_nodes WHERE task_id=?)=?",
        (json.dumps(task_metadata, allow_nan=False), task_id, *parameters, task_id, len(rows)))]
    # The task CAS and every graph settlement share BEGIN IMMEDIATE. Each
    # successful upsert changes one row; no partial graph is visible to readers.
    for values in updates:
        statements.append(("INSERT INTO execution_nodes "
            "(id,task_id,type,dependencies_json,status,attempts,budget_json,result_ref_json,"
            "metadata_json,created_at) SELECT ?,?,?,?,?,?,?,?,?,? WHERE changes()=1 "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status,"
            "metadata_json=excluded.metadata_json "
            "WHERE EXISTS (SELECT 1 FROM tasks WHERE id=? AND status='completed' "
            "AND json_extract(metadata_json,'$.isolated_commit_completion.request_id')=?)",
            (*values, task_id, request_id)))
    await db.execute_batch(statements)
    saved = await db.fetchone("SELECT status,metadata_json FROM tasks WHERE id=?", (task_id,))
    return (saved["status"] == "completed" and json.loads(saved["metadata_json"]).get(
        "isolated_commit_completion", {}).get("request_id") == request_id)
