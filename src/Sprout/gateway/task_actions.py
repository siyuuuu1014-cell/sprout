"""Explicit task application operations; identity is resolved by the adapter."""

from Sprout.gateway.task_queries import TaskQueries
from Sprout.orchestration.result_sync import synchronize_observed
from Sprout.orchestration.submission_observation import observe_submission


class TaskActions:
    def __init__(self, runtime):
        self._runtime = runtime
        self._queries = TaskQueries(runtime)

    async def sync(self, task_id, *, principal, submission_id=None, wait_seconds=0):
        _, record = await self._queries.accepted_submission(
            task_id, principal=principal, submission_id=submission_id)
        return await synchronize_observed(
            record, observer=observe_submission,
            writer=self._runtime.synchronize_task_result, wait_seconds=wait_seconds,
        )

    async def submit(self, task_id, *, principal):
        await self._queries._owned_task(task_id, principal)
        return await self._runtime.submit_persisted_task(task_id)

    async def retry_submission(self, task_id, *, principal, submission_id):
        await self._queries._owned_task(task_id, principal)
        return await self._runtime.retry_pending_submission(task_id, submission_id=submission_id)
