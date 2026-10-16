"""Transport-neutral, owner-bound task queries. No execution or reconciliation."""

from Sprout.gateway.identity import Principal
from Sprout.orchestration.submission_observation import observe_submission
from Sprout.task.reporting import task_stage


class TaskQueries:
    """Use an injected Runtime read port, never client-supplied database paths.

    Principals must come from a trusted adapter. Equal names across identity
    sources do not imply account linking. Legacy unauthenticated tasks remain
    available through their existing entry points, not this new API.
    """

    def __init__(self, runtime):
        self._runtime = runtime

    async def _owned_task(self, task_id, principal):
        if (principal is None or not principal.authenticated
                or not principal.source or not principal.user_id):
            raise PermissionError("An authenticated adapter identity is required")
        task = await self._runtime.get_task(task_id)
        if task is None:
            raise LookupError(f"Task not found: {task_id}")
        owner = task.actor
        if (not owner.authenticated or owner.user_id != principal.user_id
                or owner.source != principal.source):
            raise PermissionError("Task owner identity does not match")
        return task

    async def report(self, task_id, *, principal: Principal):
        task = await self._owned_task(task_id, principal)
        execution = await self._runtime.get_task_execution_report(task_id)
        submissions = await self._runtime.get_task_submissions(task_id)
        stage, action = task_stage(task.status.value, [
            {**node, "ownership": node.get("execution_state")}
            for node in execution["nodes"]
        ], submissions)
        return {
            "task_id": task.id,
            "task_status": task.status.value,
            "execution": execution,
            "stage": stage,
            "next_action": action,
            "budget": await self._runtime.get_task_budget_report(task_id),
            "verification": await self._runtime.get_verification_report(task_id),
            "submissions": submissions,
            "read_only": True,
            "execution_authority": False,
            "automatic_retry": False,
            "snapshot_scope": "separate observations; not globally atomic",
        }

    async def accepted_submission(self, task_id, *, principal: Principal, submission_id=None):
        task = await self._owned_task(task_id, principal)
        records = await self._runtime.get_task_submissions(task_id)
        if submission_id is not None:
            records = [record for record in records if record["id"] == submission_id]
        elif any(record["state"] != "resolved" for record in records):
            records = [record for record in records if record["state"] != "resolved"]
        if len(records) != 1 or records[0].get("task_id") != task_id:
            raise ValueError("Select an exact existing submission for this task")
        return task, records[0]

    async def observe(self, task_id, *, principal: Principal,
                      submission_id=None, wait_seconds=0):
        task, record = await self.accepted_submission(
            task_id, principal=principal, submission_id=submission_id)
        observed = await observe_submission(record, wait_seconds=wait_seconds)
        return {"task_id": task_id, "persisted_task_status": task.status.value, **observed}
