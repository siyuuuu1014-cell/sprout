"""Shared explicit observation-to-projection operation, never a task submission."""

import inspect


async def synchronize_observed(record, *, observer, writer, wait_seconds=0):
    observed = await observer(record, wait_seconds=wait_seconds)
    if observed["observation"] not in {"workflow_result", "persisted_result"}:
        return {**observed, "synchronized": False, "task_id": record.get("task_id")}
    result = writer(record, observed["result"])
    if inspect.isawaitable(result):
        result = await result
    return {**result, "task_id": record["task_id"], "synchronized": True,
            "workflow_submitted": False, "git_executed": False, "published": False}
