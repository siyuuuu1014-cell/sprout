"""Host-side JSON oracle with durable ownership and one Docker container per case."""

import asyncio
import base64
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from Sprout.execution import function_worker
from Sprout.execution.docker_verification import DockerVerificationBackend, image_ready
from Sprout.execution.verification_backend import VerificationRequest
from Sprout.execution.verification_run import input_manifest

MAX_OUTPUT_BYTES = 64 * 1024


def judge_actual(process, expected):
    """Treat stdout as an untrusted actual value, not a self-reported pass verdict."""
    if (process.allowed is not True or type(process.exit_code) is not int
            or process.exit_code != 0 or process.error or process.redactions
            or len(process.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES):
        raise ValueError("Actual output unavailable, transformed, or oversized")
    def reject_constant(value):
        raise ValueError("Nonfinite JSON value")
    actual = json.loads(process.stdout, parse_constant=reject_constant)
    # JSON canonical equality keeps booleans distinct from integers; object order is irrelevant.
    def canonical(value):
        return json.dumps(value, sort_keys=True, allow_nan=False)
    return {"actual": actual, "passed": canonical(actual) == canonical(expected)}


class FunctionVerificationRun:
    def __init__(self, metadata):
        self.metadata = metadata

    async def execute(self, runtime, *, task_id, original, exported, image, case, oracle_paths,
                      purpose="acceptance"):
        if purpose not in {"acceptance", "regression"}:
            raise ValueError("Explicit function measurement purpose required")
        if not oracle_paths:
            raise ValueError("Explicit frozen oracle paths required")
        for name in oracle_paths:
            relative = Path(name)
            if (relative.is_absolute() or ".." in relative.parts or not relative.parts
                    or (exported / relative).exists() or (exported / relative).is_symlink()):
                raise ValueError("Function input must exclude frozen oracle paths")
        worker = Path(function_worker.__file__).resolve()
        inputs = {"target": case.target, "args": case.args, "kwargs": case.kwargs}
        encoded = base64.b64encode(json.dumps(inputs, allow_nan=False).encode()).decode()
        if len(encoded) > MAX_OUTPUT_BYTES:
            raise ValueError("Function input too large")
        request = VerificationRequest(task_id, original, exported, worker, image, (encoded,))
        backend = DockerVerificationBackend()
        command = backend.plan(request)
        manifest = input_manifest(exported)
        contract = {"request": asdict(request), "input_manifest": manifest,
                    "worker_hash": hashlib.sha256(worker.read_bytes()).hexdigest(),
                    "case": asdict(case), "oracle_paths": list(oracle_paths), "purpose": purpose}
        signature = hashlib.sha256(
            json.dumps(contract, sort_keys=True, default=str).encode()).hexdigest()
        identity = str(uuid5(NAMESPACE_URL, task_id + ":function-" + purpose + ":" + case.id))
        waiting = {"execution_id": identity, "waiting": True,
                   "reason": "function_result_unconfirmed", "validation_committed": False}
        saved = await self.metadata.get_verification_execution(identity)
        if saved:
            if saved["signature"] != signature:
                return {**waiting, "reason": "function_request_changed"}
            return saved["result"] if saved["state"] == "completed" else waiting
        inspection = await backend.inspect_image(runtime, task_id, image)
        if not image_ready(inspection):
            return {**waiting, "reason": "function_image_unavailable_or_unapproved"}
        owner = str(uuid4())
        record = {"schema_version": 3, "protocol": "function_json_v1", "id": identity,
                  "task_id": task_id, "owner": owner, "signature": signature,
                  "command": command, "input_manifest": manifest, "contract": contract,
                  "container_name": command[command.index("--name") + 1]}
        if not await self.metadata.claim_verification_execution(record):
            return waiting
        output, state = waiting, "uncertain"
        try:
            process = await runtime.run_task_process(
                task_id, command, timeout_seconds=30, max_output_bytes=MAX_OUTPUT_BYTES)
            observation = judge_actual(process, case.expected_output)
            unchanged = input_manifest(exported) == manifest
            output = {"execution_id": identity, "case_id": case.id, "waiting": False,
                      "purpose": purpose,
                      "verification_image": image,
                      "process": asdict(process), "observation": observation,
                      "input_unchanged": unchanged, "host_oracle_passed":
                      unchanged and observation["passed"], "validation_committed": False,
                      "trusted_execution": False, "container_name": record["container_name"]}
            state = "completed"
        except asyncio.CancelledError:
            await self.metadata.finish_verification_execution(identity, owner, "uncertain", waiting)
            raise
        except Exception:
            # A failed transport may already have launched a container; never replay it.
            pass
        output = json.loads(json.dumps(output, default=str))
        if not await self.metadata.finish_verification_execution(identity, owner, state, output):
            return waiting
        return output
