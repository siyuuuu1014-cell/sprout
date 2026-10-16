"""Read-only observation of an accepted workflow; never submit or own a Worker."""

import asyncio
import math


async def observe_submission(record, *, wait_seconds=0):
    if (isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float))
            or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 60):
        raise ValueError("Observation wait must be finite and within 0..60 seconds")
    if (record.get("accepted") is not True or not record.get("run_id")
            or not all(record.get(key) for key in ("id", "host", "namespace", "task_queue"))):
        raise ValueError("Persisted accepted submission with exact run identity required")
    output = {"submission_id": record["id"], "run_id": record["run_id"],
              "submission_state": record["state"], "read_only": True,
              "automatic_retry": False, "new_submission": False}
    if record["state"] == "resolved":
        return {**output, "observation": "persisted_result", "result": record["result"]}
    from temporalio.client import Client, WorkflowFailureError

    try:
        client = await asyncio.wait_for(Client.connect(
            record["host"], namespace=record["namespace"]), timeout=10)
        handle = client.get_workflow_handle(record["id"], run_id=record["run_id"])
        description = await asyncio.wait_for(handle.describe(), timeout=10)
        workflow_status = description.status.name.lower()
        if workflow_status == "running" and wait_seconds == 0:
            return {**output, "observation": "running", "workflow_status": workflow_status}
        result = await asyncio.wait_for(handle.result(), timeout=wait_seconds or 10)
        return {**output, "observation": "workflow_result", "workflow_status": workflow_status,
                "result": result}
    except TimeoutError:
        return {**output, "observation": "wait_expired", "task_cancelled": False}
    except WorkflowFailureError:
        return {**output, "observation": "workflow_failed"}
    except Exception as exc:
        return {**output, "observation": "unavailable", "error_type": type(exc).__name__}
