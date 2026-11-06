"""Execute a commit activity against the existing worker-owned binding."""

from temporalio import activity
from temporalio.exceptions import ApplicationError

from Sprout.evolution.coding_assembly import build_coding_session_factory, coding_task_binding
from Sprout.evolution.replay_plan import verify_plan


async def _execute_bound_commit(bound, data):
    from Sprout.execution.commit_request_execution import execute_commit_request
    from Sprout.orchestration.isolated_worker import (
        _check_components,
        _check_experiment_components,
        commit_task_queue,
    )

    if not isinstance(data, dict) or set(data) != {"request_id", "request_fingerprint"}:
        raise ApplicationError("Exact commit request required", non_retryable=True)
    runtime = bound._runtime
    request = await runtime.storage.operational.get_commit_request(data["request_id"])
    dispatch = await runtime.storage.operational.get_commit_dispatch(data["request_id"])
    if (request is None or request["task_id"] != bound._task_id or dispatch is None
            or dispatch["workflow_id"] != activity.info().workflow_id
            or dispatch["request_fingerprint"] != data["request_fingerprint"]
            or (dispatch["host"], dispatch["namespace"], dispatch["task_queue"])
            != (bound._config.host, bound._config.namespace, bound._config.task_queue)):
        raise ApplicationError("Commit belongs to another experiment", non_retryable=True)
    task = await runtime.storage.metadata.get_task(bound._task_id)
    if (task is None or coding_task_binding(task) != bound._binding
            or getattr(runtime, "_isolated_worker_task_binding", None) != bound._binding
            or commit_task_queue(task, request["review"].get("routing_profile"))
            != bound._config.task_queue):
        raise ApplicationError("Worker or routing binding changed", non_retryable=True)
    if getattr(runtime, "_isolated_worker_role", None) == "commit_only":
        _check_experiment_components(runtime, verify_plan(bound._plan))
    else:
        _check_components(runtime, verify_plan(bound._plan))
    build_coding_session_factory(runtime, task)
    return await execute_commit_request(runtime, data["request_id"], data["request_fingerprint"])
