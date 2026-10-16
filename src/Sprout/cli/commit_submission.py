"""Explicit relay of approved decisions to the experiment's original Temporal route."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from Sprout.cli.commit_decision import _DecisionStore
from Sprout.evolution.replay_plan import verify_plan
from Sprout.gateway.local_identity import creator_blockers
from Sprout.orchestration.commit_dispatch import _submit_commit_dispatch
from Sprout.orchestration.isolated_worker import commit_task_queue, experiment_task_queue
from Sprout.storage.contracts.commit_request import commit_workflow_id
from Sprout.storage.local.sqlite.commit_status import _regular, commit_status_report
from Sprout.storage.local.sqlite.verification_report import recovery_facts


def _route(database, task_id, principal, routing_profile=None):
    facts = recovery_facts(database, task_id)
    plan = facts["metadata"].get("replay_plan")
    payload = verify_plan(plan)
    if (payload["schema_version"] != 5 or payload["execution_mode"] != "code"
            or creator_blockers(payload.get("creator"), principal)
            or Path(payload["repository_root"]).resolve().parent != database.parent):
        raise ValueError("Creator-bound experiment database required")
    expected_queue = experiment_task_queue(
        SimpleNamespace(id=task_id, metadata={"replay_plan": plan}))
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT record_json FROM task_submissions WHERE task_id=? "
                                  "LIMIT 201", (task_id,)).fetchall()
        if len(rows) > 200:
            raise ValueError("Ambiguous submission history; manual reconciliation required")
        records = [json.loads(row[0]) for row in rows]
        accepted = [record for record in records if record.get("accepted") is True]
        routes = set()
        for record in accepted:
            route = tuple(record.get(key) for key in ("host", "namespace", "task_queue"))
            if (record.get("task_id") != task_id or not all(isinstance(value, str) and value
                    for value in route) or route[2] != expected_queue
                    or not isinstance(record.get("run_id"), str) or not record["run_id"]):
                raise ValueError("Accepted coding submission binding mismatch")
            routes.add(route)
        if len(routes) != 1:
            raise ValueError("One accepted experiment Temporal route required")
        host, namespace, _ = next(iter(routes))
        target_queue = commit_task_queue(SimpleNamespace(id=task_id,
            metadata={"replay_plan": plan}), routing_profile)
        return host, namespace, target_queue
    except (sqlite3.Error, AttributeError, TypeError) as exc:
        raise ValueError("Accepted experiment Temporal route unavailable or malformed") from exc
    finally:
        connection.close()


async def submit_cli_commit(database, task_id, request_id, *, principal, expected_fingerprint=None):
    database = _regular(database)
    report = commit_status_report(database, task_id, principal=principal,
                                  request_id=request_id, include_review=True)
    item = report["requests"][0]
    if item["decision_state"] != "approved":
        raise ValueError("Persisted approved decision required; use commit-decide first")
    if expected_fingerprint is not None and expected_fingerprint != item["decision_fingerprint"]:
        raise ValueError("Approved request fingerprint mismatch")
    if item["status"] == "needs_reconciliation":
        raise ValueError("Unknown execution requires manual reconciliation, not resubmission")
    if item["dispatch_state"] != "accepted":
        if datetime.fromisoformat(item["expires_at"]) <= datetime.now(UTC):
            raise ValueError("Commit request expired; no new submission permitted")
        if report["task_status"] not in {"running", "waiting_approval"}:
            raise ValueError("Task is not eligible for explicit commit submission")
    host, namespace, queue = _route(database, task_id, principal,
                                    item["review"].get("routing_profile"))
    record = {"request_id": request_id, "request_fingerprint": item["decision_fingerprint"],
        "workflow_id": commit_workflow_id(request_id, item["decision_fingerprint"]),
        "host": host, "namespace": namespace, "task_queue": queue}
    operational = _regular(database.parent / "operational.db")
    try:
        connection = sqlite3.connect(operational.as_uri() + "?mode=rw", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise ValueError("Existing dispatch database cannot be opened") from exc
    connection.row_factory = sqlite3.Row
    try:
        result = await _submit_commit_dispatch(_DecisionStore(connection), record)
        return {**result, "request_id": request_id, "workflow_id": record["workflow_id"],
                "local_execution": False}
    except sqlite3.Error as exc:
        raise ValueError("Commit dispatch storage unavailable; no local execution") from exc
    finally:
        connection.close()
