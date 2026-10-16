"""Node-owned, authorized Docker coding execution with no fallback or retry."""

import asyncio
import hashlib
import json
from dataclasses import asdict
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.coding_container import coding_image_ready, coding_plan
from Sprout.execution.verification_run import input_manifest
from Sprout.orchestration.models import NodeStatus


class DockerCodingBackend:
    """Construct inside a live guard callback, never reconstruct owner from the DB.

    One backend instance represents one command attempt. Durable node ownership
    remains the authority across restart; this instance's latch is only a local
    additional safeguard, not a persisted command journal.
    """

    def __init__(self, metadata, authorized_runner, *, node_id, owner, command_slot="0"):
        if not node_id or not owner:
            raise ValueError("Live node owner required")
        self._metadata = metadata
        self._runner = authorized_runner
        self._node_id, self._owner = node_id, owner
        if not isinstance(command_slot, str) or not command_slot:
            raise ValueError("Stable command slot required")
        self._slot = command_slot
        self._started = False

    async def _check(self, request, node_id):
        node = await self._metadata.get_execution_node(node_id)
        if node is None:
            raise ValueError("Persisted node required")
        guard = node.metadata.get("execution_guard", {})
        if (node_id != self._node_id or node.task_id != request.task_id
                or node.status != NodeStatus.RUNNING or guard.get("state") != "active"
                or guard.get("owner") != self._owner):
            raise ValueError("Live node ownership mismatch")

    async def execute(self, request, *, node_id):
        if self._started:
            raise ValueError("Coding command attempt already consumed")
        await self._check(request, node_id)
        identity = str(uuid5(NAMESPACE_URL, node_id + ":coding:" + self._slot))
        inputs = {"request": asdict(request), "input": input_manifest(request.exported)}
        signature = hashlib.sha256(json.dumps(
            inputs,
            sort_keys=True, default=str).encode()).hexdigest()
        current = await self._metadata.get_coding_execution(identity)
        if current is not None:
            # Writable input may have changed because the first command succeeded.
            # No automatic cache reuse until output/input provenance is established.
            if current.get("state") != "not_started" or current.get("signature") != signature:
                raise RuntimeError("Persisted coding attempt exists; do not retry")
        command = coding_plan(request)
        await self._metadata.ensure_coding_intent(json.loads(json.dumps(
            {"id": identity, "node_id": node_id, "task_id": request.task_id,
             "owner": self._owner, "slot": self._slot, "signature": signature,
             "inputs": inputs, "schema_version": 1}, default=str)))
        inspection = await self._runner.run_task_process(
            request.task_id, ("docker", "image", "inspect", request.image), timeout_seconds=15)
        if inspection.needs_approval:
            return inspection
        if not coding_image_ready(inspection):
            raise ValueError("Coding image unavailable, unapproved, or incompatible")
        authorize = getattr(self._runner, "authorize_task_process", None)
        if not callable(authorize):
            raise ValueError("Coding authorization preflight unavailable")
        authorization = await authorize(request.task_id, command)
        if not authorization.allowed:
            return authorization
        await self._check(request, node_id)
        # Set before the first mutating await, so concurrent callers cannot launch twice.
        if self._started:
            raise ValueError("Coding command attempt already consumed")
        self._started = True
        record = {"id": identity, "task_id": request.task_id, "node_id": node_id,
                  "owner": self._owner, "slot": self._slot, "signature": signature,
                  "command": command, "schema_version": 1}
        if not await self._metadata.claim_coding_execution(record):
            raise RuntimeError("Coding claim changed; do not execute")
        try:
            result = await self._runner.run_task_process(
                request.task_id, command, timeout_seconds=60, max_output_bytes=64 * 1024)
            if result.allowed and (result.exit_code is None or result.error):
                raise RuntimeError("Coding execution result unconfirmed; do not retry")
        except BaseException:
            try:
                await asyncio.shield(self._metadata.finish_coding_execution(
                    identity, self._owner, "uncertain", {"reason": "coding_result_unconfirmed"}))
            except Exception:
                pass  # An active durable claim still blocks all repeats.
            raise
        state = "completed"
        if not result.allowed:
            if result.started is not False:
                await self._metadata.finish_coding_execution(
                    identity, self._owner, "uncertain", asdict(result))
                raise RuntimeError("Missing broker evidence of no launch; do not retry")
            state = "not_started"
        if not await self._metadata.finish_coding_execution(
            identity, self._owner, state, asdict(result)):
            raise RuntimeError("Coding settlement unconfirmed; do not retry")
        return result
