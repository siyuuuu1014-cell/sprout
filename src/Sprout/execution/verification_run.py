"""Durable Docker observation ownership in the existing metadata authority."""

import asyncio
import hashlib
import json
from dataclasses import asdict
from uuid import NAMESPACE_URL, uuid4, uuid5

from Sprout.execution.docker_verification import DockerVerificationBackend, image_ready
from Sprout.execution.verification_backend import VerificationRequest


def input_manifest(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Verification input changed to a link")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


class DockerVerificationRun:
    def __init__(self, metadata, summarize_report):
        self.metadata = metadata
        self.summarize_report = summarize_report

    async def execute(self, runtime, request: VerificationRequest, *, slot: str) -> dict:
        backend = DockerVerificationBackend()
        command = backend.plan(request)
        if len(request.arguments) < 2 or request.arguments[0] != "-":
            raise ValueError("Container reports must use nonce-bound stdout transport")
        manifest = input_manifest(request.input_root)
        probe_hash = hashlib.sha256(request.observer.read_bytes()).hexdigest()
        signature = hashlib.sha256(
            json.dumps(
                {"request": asdict(request), "input": manifest, "observer_hash": probe_hash},
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        execution_id = str(uuid5(NAMESPACE_URL, request.task_id + ":verification:" + slot))
        waiting = {
            "waiting": True,
            "execution_id": execution_id,
            "reason": "verification_result_unconfirmed",
            "trusted_execution": False,
        }
        current = await self.metadata.get_verification_execution(execution_id)
        if current:
            if current["signature"] != signature:
                return {**waiting, "reason": "verification_request_changed"}
            return current["result"] if current["state"] == "completed" else waiting
        inspection = await backend.inspect_image(runtime, request.task_id, request.image)
        if not inspection.allowed or inspection.exit_code != 0:
            return {**waiting, "reason": "verification_image_unavailable_or_unapproved"}
        if not image_ready(inspection):
            return {**waiting, "reason": "verification_image_not_commissioned"}
        owner = str(uuid4())
        record = {
            "schema_version": 2,
            "slot": slot,
            "nonce": request.arguments[1],
            "observer_hash": probe_hash,
            "id": execution_id,
            "task_id": request.task_id,
            "owner": owner,
            "signature": signature,
            "command": command,
            "input_manifest": manifest,
            "container_name": command[command.index("--name") + 1],
        }
        if not await self.metadata.claim_verification_execution(record):
            return waiting
        output, state = waiting, "uncertain"
        try:
            result = await runtime.run_task_process(request.task_id, command, timeout_seconds=60)
            nonce = request.arguments[1]
            marker = "SPROUT_PYTEST_REPORT " + nonce + " "
            lines = [
                line[len(marker) :]
                for line in result.stdout.splitlines()
                if line.startswith(marker)
            ]
            if result.allowed and result.exit_code is not None and len(lines) == 1:
                payload = json.loads(lines[0])
                summary = self.summarize_report(payload, nonce, result.exit_code, probe_hash)
                if "observer_identity_mismatch" not in summary["blockers"]:
                    output = {
                        "execution_id": execution_id,
                        "process": asdict(result),
                        "pytest_observation": summary,
                        "container_name": record["container_name"],
                        "input_unchanged": input_manifest(request.input_root) == manifest,
                        "waiting": False,
                        "trusted_execution": False,
                    }
                    state = "completed"
        except asyncio.CancelledError:
            await self.metadata.finish_verification_execution(
                execution_id, owner, "uncertain", waiting
            )
            raise
        except Exception:
            # No implied rollback or replay: the daemon may still be running.
            pass
        output = json.loads(json.dumps(output, default=str))
        if not await self.metadata.finish_verification_execution(
            execution_id, owner, state, output
        ):
            return waiting
        return output
