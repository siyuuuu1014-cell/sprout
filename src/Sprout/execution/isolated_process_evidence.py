"""Shared read-only reconstruction of one persisted node/container observation."""

import json
import re
from dataclasses import asdict
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.coding_container import coding_plan
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.execution.models import ProcessResult


async def inspect_coding_process(metadata, *, request, node, slot, baseline):
    identity = str(uuid5(NAMESPACE_URL, node.id + ":coding:" + slot))
    intent = await metadata.get_coding_intent(identity)
    record = await metadata.get_coding_execution(identity)
    inputs = json.loads(json.dumps({"request": asdict(request), "input": baseline}, default=str))
    signature = fingerprint(inputs)
    owner = node.metadata["execution_guard"]["owner"]
    if (not intent or not record or record.get("state") != "completed"
            or any(item.get("id") != identity or item.get("task_id") != node.task_id
                   or item.get("node_id") != node.id or item.get("owner") != owner
                   or item.get("signature") != signature for item in (intent, record))
            or intent.get("inputs") != inputs or record.get("slot") != slot):
        raise ValueError("completed_case_execution_required:" + slot)
    launch = list(coding_plan(request))
    recorded = record["command"]
    name_index = launch.index("--name") + 1
    if (len(recorded) != len(launch)
            or not re.fullmatch(r"sprout-coding-[0-9a-f]{32}", recorded[name_index])):
        raise ValueError("case_container_plan_mismatch:" + slot)
    launch[name_index] = recorded[name_index]
    raw = record["result"]
    if (launch != list(recorded) or raw.get("started") is not True
            or list(raw.get("command", [])) != launch):
        raise ValueError("case_process_provenance_mismatch:" + slot)
    process = ProcessResult(**raw)
    if (process.allowed is not True or type(process.exit_code) is not int
            or process.error or process.redactions
            or len((process.stdout + process.stderr).encode()) > 64 * 1024):
        raise ValueError("case_process_result_unusable:" + slot)
    return process, {"execution_id": identity, "signature": signature,
                     "process_fingerprint": fingerprint(raw)}
