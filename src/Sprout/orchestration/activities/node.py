"""Runtime-backed node activities."""

from __future__ import annotations

from typing import Any

from temporalio import activity

from Sprout.orchestration.temporal_codec import node_from_dict

_activity_runtime = None


def set_activity_runtime(runtime) -> None:
    global _activity_runtime
    _activity_runtime = runtime


@activity.defn
async def run_sprout_node(node: dict[str, Any]) -> dict[str, Any]:
    """Execute one real project node through the worker's Runtime."""
    return await _execute_node(_activity_runtime, node)


async def _execute_node(runtime, node):
    if runtime is None:
        raise RuntimeError("Sprout worker has no runtime configured")
    if activity.in_activity():
        metadata = getattr(getattr(runtime, "storage", None), "metadata", None)
        confirm = getattr(metadata, "confirm_temporal_activity", None)
        info = activity.info()
        if confirm is not None:
            bound = (await metadata.get_task_submission(info.workflow_id)
                     if info.workflow_id else None)
            if bound is not None and bound["task_id"] != node["task_id"]:
                raise PermissionError("Temporal activity task does not match persisted submission")
            records = await metadata.list_task_submissions(node["task_id"])
            for record in records:
                if record["id"] != info.workflow_id:
                    continue
                if (not info.workflow_id or not info.workflow_run_id
                        or info.is_local or info.workflow_type != "SproutExecutionWorkflow"
                        or info.activity_type != "run_sprout_node"
                        or record["namespace"] != info.namespace
                        or record["task_queue"] != info.task_queue
                        or record.get("run_id") not in {None, info.workflow_run_id}
                        or node not in record["payload"]["nodes"]):
                    raise PermissionError("Temporal activity does not match persisted submission")
                if not await confirm(record, info.workflow_run_id):
                    raise PermissionError("Temporal confirmation failed for persisted submission")
                break
            else:
                if records:
                    raise PermissionError("Temporal workflow does not own persisted submission")
    return await runtime.execute_node(node_from_dict(node))


def bind_node_activity(runtime):
    """Keep the ordinary task worker bound to its owned Runtime."""
    @activity.defn(name="run_sprout_node")
    async def execute_node(node):
        return await _execute_node(runtime, node)

    return execute_node
