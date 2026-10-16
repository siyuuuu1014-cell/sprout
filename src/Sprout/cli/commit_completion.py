"""Explicit database-only recovery after a trusted completed Git commit."""

import sqlite3

from Sprout.cli.commit_decision import _DecisionStore
from Sprout.storage.local.sqlite.commit_completion import complete_commit_task
from Sprout.storage.local.sqlite.commit_status import _regular, commit_status_report


async def finalize_cli_commit(database, task_id, request_id, *, principal):
    database = _regular(database)
    report = commit_status_report(database, task_id, principal=principal, request_id=request_id,
                                  include_review=True)
    item = report["requests"][0]
    if item["status"] != "applied":
        raise ValueError("Trusted applied result required; finalization cannot execute Git")
    authority = _regular(database.parent / "operational.db")
    operations = sqlite3.connect(authority.as_uri() + "?mode=ro", uri=True)
    operations.row_factory = sqlite3.Row
    try:
        request = await _DecisionStore(operations).get_commit_request(request_id)
        if (request is None or request["task_id"] != task_id
                or request["fingerprint"] != item["decision_fingerprint"]):
            raise ValueError("Completed request binding changed")
    finally:
        operations.close()
    connection = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        completed = await complete_commit_task(_DecisionStore(connection), task_id, request_id,
            request["fingerprint"], request["review"]["evaluation_node_id"])
    except sqlite3.Error as exc:
        raise ValueError("Task settlement unavailable; Git was not re-executed") from exc
    finally:
        connection.close()
    return {"task_id": task_id, "request_id": request_id, "applied": True,
            "task_completed": completed, "git_executed": False,
            "reason": "completed" if completed else "task_graph_not_ready"}
