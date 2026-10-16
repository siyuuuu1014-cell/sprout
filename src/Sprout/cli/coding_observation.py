"""Creator-bound SQLite reads plus workflow observation; no mutable Runtime."""

import json
import sqlite3

from Sprout.cli.commit_submission import _route
from Sprout.orchestration.submission_observation import observe_submission
from Sprout.storage.local.sqlite.commit_status import _regular
from Sprout.storage.local.sqlite.verification_report import recovery_facts


def accepted_record(database, task_id, *, principal, submission_id=None):
    database = _regular(database)
    _route(database, task_id, principal)  # Validates creator and original accepted route.
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT record_json FROM task_submissions WHERE task_id=? "
                                  "LIMIT 201", (task_id,)).fetchall()
        if len(rows) > 200:
            raise ValueError("Ambiguous submission history")
        records = [json.loads(row[0]) for row in rows]
        if submission_id is not None:
            records = [record for record in records if record["id"] == submission_id]
        elif any(record["state"] != "resolved" for record in records):
            records = [record for record in records if record["state"] != "resolved"]
        if len(records) != 1:
            raise ValueError("Select an exact existing submission with --submission-id")
    finally:
        connection.close()
    return records[0]


async def coding_status(database, task_id, *, principal, submission_id=None, wait_seconds=0):
    record = accepted_record(database, task_id, principal=principal, submission_id=submission_id)
    facts = recovery_facts(database, task_id)
    observed = await observe_submission(record, wait_seconds=wait_seconds)
    return {"task_id": task_id, "persisted_task_status": facts["status"], **observed,
            "next_action": "Observation does not resume tasks or reconcile unknown side effects"}
