"""Atomic workflow result/task projection within metadata.db only."""

import json
import sqlite3

from Sprout.runtime.state import TaskStateMachine
from Sprout.task.models import TaskStatus


def synchronize_result(database, expected, result):
    allowed = {"completed", "failed", "cancelled", "waiting_approval", "waiting_resource"}
    if not isinstance(result, dict) or result.get("status") not in allowed:
        raise ValueError("Final structured workflow result required")
    summary = {key: result.get(key) for key in ("status", "error", "node_id")}
    connection = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT record_json FROM task_submissions "
            "WHERE id=? AND task_id=?", (expected["id"], expected["task_id"])).fetchone()
        if row is None:
            raise ValueError("Persisted submission disappeared")
        current = json.loads(row[0])
        if (current.get("accepted") is not True or not current.get("run_id")
                or any(current.get(key) != expected.get(key) for key in
                       ("id", "task_id", "run_id", "host", "namespace", "task_queue", "payload"))):
            raise ValueError("Accepted submission binding changed")
        task = connection.execute("SELECT status FROM tasks WHERE id=?",
                                  (expected["task_id"],)).fetchone()
        if task is None:
            raise ValueError("Task disappeared")
        if current["state"] == "resolved":
            if any(current["result"].get(key) != summary[key] for key in summary):
                raise ValueError("Confirmed workflow result cannot be replaced")
        else:
            connection.execute("UPDATE task_submissions SET record_json=? WHERE id=?",
                (json.dumps({**current, "state": "resolved", "result": summary}, allow_nan=False),
                 current["id"]))
        latest = connection.execute("SELECT id FROM task_submissions WHERE task_id=? "
            "ORDER BY rowid DESC LIMIT 1", (expected["task_id"],)).fetchone()
        before, target = TaskStatus(task[0]), TaskStatus(summary["status"])
        updated = False
        can_project = TaskStateMachine.can_transition(before, target) or (
            before is TaskStatus.WAITING_RESOURCE
            and TaskStateMachine.can_transition(before, TaskStatus.RUNNING)
            and TaskStateMachine.can_transition(TaskStatus.RUNNING, target))
        if latest[0] == current["id"] and can_project:
            if target is TaskStatus.COMPLETED:
                # Workflow completion must not manufacture completed graph nodes or Git evidence.
                graph = current.get("payload", {}).get("nodes", [])
                if not graph:
                    raise ValueError("Completed task requires persisted graph evidence")
                for node in graph:
                    saved = connection.execute("SELECT status,metadata_json FROM execution_nodes "
                        "WHERE id=? AND task_id=?", (node["id"], expected["task_id"])).fetchone()
                    if (saved is None or saved[0] not in {"completed", "skipped"}
                            or (saved[0] == "completed" and json.loads(saved[1]).get(
                                "execution_guard", {}).get("state") != "completed")):
                        raise ValueError("Completed task graph evidence is incomplete")
            connection.execute("UPDATE tasks SET status=? WHERE id=?",
                               (target.value, expected["task_id"]))
            updated = True
        connection.commit()
        return {"submission_id": current["id"], "submission_state": "resolved",
                "task_status": target.value if updated else before.value,
                "task_status_updated": updated, "result_status": target.value,
                "database_role": "metadata", "nodes_modified": False}
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
