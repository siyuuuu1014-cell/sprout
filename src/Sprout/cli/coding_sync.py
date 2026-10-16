"""Explicit result synchronization; status remains read-only."""

from Sprout.cli.coding_observation import accepted_record
from Sprout.orchestration.result_sync import synchronize_observed
from Sprout.orchestration.submission_observation import observe_submission
from Sprout.storage.local.sqlite.commit_status import _regular
from Sprout.storage.local.sqlite.submission_sync import synchronize_result


async def sync_coding(database, task_id, *, principal, submission_id=None, wait_seconds=0):
    database = _regular(database)
    record = accepted_record(database, task_id, principal=principal, submission_id=submission_id)
    result = await synchronize_observed(
        record, observer=observe_submission,
        writer=lambda expected, value: synchronize_result(database, expected, value),
        wait_seconds=wait_seconds,
    )
    return {**result, "task_id": task_id}
