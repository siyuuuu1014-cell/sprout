"""Internal durable Temporal delivery, never local execution or Git authorization.

The matching dedicated commit workflow/worker must be commissioned separately.
Used by explicit trusted CLI relay; not a model tool or background poller.
"""

import asyncio
from datetime import timedelta

from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.storage.contracts.commit_request import validate_dispatch


async def _submit_commit_dispatch(operational, record):
    from temporalio.client import Client
    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError

    validate_dispatch(record)
    saved = await operational.ensure_commit_dispatch(record)
    if saved["state"] == "accepted":
        return {"dispatch_state": "accepted", "run_id": saved["run_id"],
                "execution_confirmed": False}
    phase = "connect"
    try:
        client = await asyncio.wait_for(Client.connect(record["host"],
            namespace=record["namespace"]), timeout=10)
        phase = "start"
        payload = {"request_id": record["request_id"],
                   "request_fingerprint": record["request_fingerprint"]}
        try:
            handle = await asyncio.wait_for(client.start_workflow("SproutExperimentCommitWorkflow",
                payload, id=record["workflow_id"], task_queue=record["task_queue"],
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                memo={"sprout_commit_dispatch": fingerprint(record)}), timeout=10)
            run_id = getattr(handle, "first_execution_run_id", None)
        except WorkflowAlreadyStartedError as exc:
            handle = client.get_workflow_handle(record["workflow_id"], run_id=exc.run_id)
            description = await asyncio.wait_for(handle.describe(), timeout=10)
            memo = await description.memo()
            if (description.id != record["workflow_id"]
                    or description.namespace != record["namespace"]
                    or description.task_queue != record["task_queue"]
                    or description.raw_info.type.name != "SproutExperimentCommitWorkflow"
                    or memo.get("sprout_commit_dispatch") != fingerprint(record)):
                raise ValueError("Existing workflow binding mismatch") from exc
            history = await asyncio.wait_for(handle.fetch_history(
                rpc_timeout=timedelta(seconds=10)), timeout=10)
            if not history.events or not history.events[0].HasField(
                    "workflow_execution_started_event_attributes"):
                raise ValueError("Existing workflow input unavailable") from exc
            arguments = await client.data_converter.decode(history.events[0]
                .workflow_execution_started_event_attributes.input.payloads)
            if arguments != [payload]:
                raise ValueError("Existing workflow input mismatch") from exc
            run_id = description.run_id
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("Temporal run identity unavailable")
        await operational.mark_commit_dispatch(record["request_id"], "accepted", run_id)
    except BaseException as exc:
        # Unknown/mismatched server binding never authorizes local execution.
        state = "pending" if phase == "connect" else "uncertain"
        await asyncio.shield(operational.mark_commit_dispatch(record["request_id"], state))
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        persisted = await operational.get_commit_dispatch(record["request_id"])
        return {"dispatch_state": persisted["state"], "execution_confirmed": False,
                "reason": "temporal_delivery_unconfirmed", "local_fallback": False}
    persisted = await operational.get_commit_dispatch(record["request_id"])
    return {"dispatch_state": persisted["state"], "run_id": persisted["run_id"],
            "execution_confirmed": False}
