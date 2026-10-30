"""Durable Docker observation ownership in the existing metadata authority."""

import asyncio
import hashlib
import json
from dataclasses import asdict
from uuid import NAMESPACE_URL, uuid4, uuid5

from Sprout.execution.docker_verification import DockerVerificationBackend, image_ready
from Sprout.execution.verification_backend import VerificationRequest


class _UnverifiedPytest(ValueError):
    """A validated process observation is not an independent pytest verdict."""

    def __init__(self, evidence):
        super().__init__("verification_pytest_independent_observation_unavailable")
        self.evidence = evidence


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

    @staticmethod
    def prepare(request, *, node_id, owner, manifest_sha, input_inventory=None):
        """No daemon access, image inspection, claim, approval, or execution."""
        command = DockerVerificationBackend().plan(request, ownership={
            "task": request.task_id, "node": node_id, "owner": owner, "manifest": manifest_sha})
        return {"state": "prepared", "task_id": request.task_id, "node_id": node_id,
                "owner": owner, "manifest": manifest_sha, "command": command,
                "image": request.image,
                "container_name": command[command.index("--name") + 1],
                "input_manifest": (dict(input_inventory) if input_inventory is not None
                                   else input_manifest(request.input_root)),
                "observer_hash": hashlib.sha256(request.observer.read_bytes()).hexdigest(),
                "observer_path": str(request.observer),
                "container_id": None, "native_start_allowed": False, "verified": False}

    @staticmethod
    def check_preparation(plan):
        from pathlib import Path

        from Sprout.execution.docker_verification import safe_path

        observer = safe_path(Path(plan["observer_path"]))
        if hashlib.sha256(observer.read_bytes()).hexdigest() != plan["observer_hash"]:
            raise PermissionError("verification_observer_changed")
        if plan.get("protocol") == "observed-command-v1":
            probe = safe_path(Path(plan["pytest_observer_path"]))
            if (hashlib.sha256(probe.read_bytes()).hexdigest()
                    != plan["request"]["pytest_observer_sha"]):
                raise PermissionError("verification_pytest_observer_changed")

    @staticmethod
    def observed_container(plan, payload, *, expected_id=None):
        """Validate a future read-only inspect result; never infer from CLI exit."""
        import re

        if not isinstance(payload, dict):
            raise ValueError("verification_container_inspection_missing")
        identity = payload.get("Id", "")
        config = payload.get("Config")
        if (type(config) is not dict or type(config.get("Labels")) is not dict
                or type(payload.get("State")) is not dict):
            raise ValueError("verification_container_inspection_schema")
        labels = config["Labels"]
        if (type(identity) is not str or not re.fullmatch(r"[0-9a-f]{64}", identity)
                or (expected_id is not None and identity != expected_id)
                or payload.get("Name") != "/" + plan["container_name"]
                or payload.get("Image") != plan["image"]
                or labels.get("sprout.role") != "independent-verification"
                or any(labels.get("sprout.verification." + key) != plan[field]
                       for key, field in (("task", "task_id"), ("node", "node_id"),
                                          ("owner", "owner"), ("manifest", "manifest")))):
            raise ValueError("verification_container_identity_mismatch")
        state = payload.get("State", {})
        stopped = (state.get("Running") is False and type(state.get("Pid")) is int
                   and state["Pid"] == 0 and type(state.get("ExitCode")) is int
                   and state.get("Status") in {"exited", "dead"})
        return {"container_id": identity, "stopped_observed": stopped,
                "state": "stopped_observed" if stopped else "UNKNOWN",
                "retry_allowed": False, "verified": False}

    @staticmethod
    def prepare_command(request, *, node_id, owner, manifest_sha, writable_input=False,
                        protected_paths=(), input_inventory=None):
        """Structured command worker plan, never a subprocess/daemon call."""
        plan = DockerVerificationRun.prepare(request, node_id=node_id, owner=owner,
            manifest_sha=manifest_sha, input_inventory=input_inventory)
        if writable_input:
            if (not protected_paths or any((request.input_root / path).exists()
                                          for path in protected_paths)):
                raise ValueError("verification_writable_copy_contains_frozen_oracles")
            before = f"type=bind,src={request.input_root},dst=/workspace,readonly"
            command = list(plan["command"])
            command[command.index(before)] = before.removesuffix(",readonly")
            plan["command"] = tuple(command)
        plan["protocol"] = "structured-command-v1"
        plan["input_mode"] = "disposable-copy" if writable_input else "readonly"
        return plan

    @staticmethod
    def prepare_observed(
        request,
        *,
        logical_argv,
        tools,
        obligation,
        node_id,
        owner,
        manifest_sha,
        input_inventory,
        writable_input=False,
        protected_paths=(),
        pytest_contract=None,
    ):
        import base64
        import re
        from pathlib import Path

        from Sprout.evolution import pytest_probe
        from Sprout.execution import verification_observer
        from Sprout.execution.verification_manifest import digest

        for key in {"python", logical_argv[0]}:
            identity = tools.get(key, {})
            if (
                type(identity) is not dict
                or not isinstance(identity.get("path"), str)
                or not identity["path"].startswith("/usr/")
                or not re.fullmatch(r"[0-9a-f]{64}", identity.get("sha256", ""))
            ):
                raise ValueError("verification_tool_binding_unavailable")
        nonce = uuid4().hex
        context = {
            "task": request.task_id,
            "node": node_id,
            "owner": owner,
            "manifest": manifest_sha,
            "image": request.image,
            "obligation": obligation,
        }
        payload = {
            "context": context,
            "nonce": nonce,
            "argv": list(logical_argv),
            "cwd": "/workspace",
            "tools": tools,
            "max_seconds": 50,
            "max_output": 65536,
            "pytest_observer_sha": hashlib.sha256(
                Path(pytest_probe.__file__).read_bytes()
            ).hexdigest(),
            "pytest_contract": pytest_contract,
            "materialize": dict(input_inventory) if writable_input else None,
        }
        encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
        request = VerificationRequest(
            request.task_id,
            request.source_root,
            request.input_root,
            Path(verification_observer.__file__),
            request.image,
            (encoded,),
        )
        plan = DockerVerificationRun.prepare_command(
            request,
            node_id=node_id,
            owner=owner,
            manifest_sha=manifest_sha,
            input_inventory=input_inventory,
            writable_input=writable_input,
            protected_paths=protected_paths,
        )
        command = list(plan["command"])
        if writable_input:
            original = f"type=bind,src={request.input_root},dst=/workspace"
            command[command.index(original)] = (
                f"type=bind,src={request.input_root},dst=/inputs,readonly")
            command.insert(command.index(request.image),
                "--tmpfs=/workspace:rw,noexec,nosuid,nodev,size=4m,mode=1777")
        command[command.index("--entrypoint=python")] = "--entrypoint=" + tools["python"]["path"]
        index = command.index(request.image)
        command[index:index] = [
            "--mount",
            f"type=bind,src={Path(pytest_probe.__file__).resolve()},"
            "dst=/pytest_observer.py,readonly",
        ]
        plan.update(
            command=tuple(command),
            protocol="observed-command-v1",
            request=payload,
            nonce=nonce,
            request_sha=digest(payload),
            pytest_observer_path=str(Path(pytest_probe.__file__).resolve()),
        )
        return plan

    @staticmethod
    def command_observation(plan, stdout, exit_code, inspection):
        import math

        if not isinstance(plan, dict) or "nonce" not in plan:
            raise ValueError("verification_observation_plan_missing")
        observed = DockerVerificationRun.observed_container(
            plan, inspection, expected_id=plan.get("container_id")
        )
        if not observed["stopped_observed"] or exit_code != inspection["State"]["ExitCode"]:
            raise ValueError("verification_container_not_confirmed")
        marker = "SPROUT_COMMAND_REPORT " + plan["nonce"] + " "
        lines = stdout.splitlines()
        if len(lines) != 1 or not lines[0].startswith(marker):
            raise ValueError("verification_observer_protocol")
        value = json.loads(lines[0][len(marker) :])
        request = plan["request"]
        if (
            type(value) is not dict
            or type(value.get("schema_version")) is not int or value.get("schema_version") != 1
            or any(value.get(k) != request[k] for k in ("context", "nonce", "argv", "cwd", "tools"))
            or value.get("observer_sha") != plan["observer_hash"]
            or type(value.get("exit_code")) is not int
            or value["exit_code"] != exit_code
            or type(value.get("duration_ms")) not in {int, float}
            or not math.isfinite(value["duration_ms"])
            or value["duration_ms"] < 0
            or value.get("status") not in {"completed", "timeout", "output_limit"}
            or any(type(value.get(k)) is not str for k in ("stdout", "stderr"))
            or sum(len(value[k].encode()) for k in ("stdout", "stderr")) > 6 * 65536
        ):
            raise ValueError("verification_observer_identity")
        if value["status"] == "timeout":
            outcome = "timeout"
        elif value["status"] != "completed":
            outcome = "runner_error"
        else:
            outcome = "passed" if exit_code == 0 else "check_failed"
        argv = request["argv"]
        if outcome == "passed" and (argv[0] == "pytest" or argv[:3] == ["python", "-m", "pytest"]):
            marker = "SPROUT_PYTEST_REPORT " + plan["nonce"] + " "
            probes = [
                s[len(marker) :] for s in value["stdout"].splitlines() if s.startswith(marker)
            ]
            if len(probes) != 1:
                raise ValueError("verification_pytest_observation_missing")
            probe = json.loads(probes[0])
            contract = request.get("pytest_contract")
            configuration = probe.get("configuration", {})
            if (not isinstance(contract, dict) or type(contract.get("plugins")) is not list
                    or {name.split("::", 1)[0] for name in probe.get("collected", ())}
                    != set(contract["files"])):
                raise ValueError("verification_pytest_configuration_unproved")
            config = configuration.get("relative_config")
            if (config != contract["ini_file"]
                    or config is not None and configuration.get("config_hash")
                    != contract["configs"].get(config)):
                raise ValueError("verification_pytest_config_changed")
            if (
                probe.get("nonce") != plan["nonce"]
                or probe.get("exit_code") != 0
                or probe.get("runner", {}).get("probe_hash") != request["pytest_observer_sha"]
                or not probe.get("collected")
                or probe.get("collection_errors")
                or any(
                    p.get("outcome") != "passed" or p.get("wasxfail")
                    for p in probe.get("reports", ())
                )
                or set(probe["collected"])
                != {p["nodeid"] for p in probe.get("reports", ()) if p.get("phase") == "call"}
            ):
                raise ValueError("verification_pytest_observation_incomplete")
            if (isinstance(contract, dict) and type(contract.get("plugins")) is list
                    and isinstance(configuration, dict)
                    and type(configuration.get("plugins")) is list
                    and configuration["plugins"] != contract["plugins"]):
                # Configuration evidence is not a test verdict. Only the owned
                # lifecycle caller may settle this after start/stop verification;
                # the pure parser still raises, and never publishes PASS.
                raise _UnverifiedPytest({
                    "outcome": "unverified", "executed": True, "passed": False,
                    "exit_code": exit_code, "stdout": value["stdout"],
                    "stderr": value["stderr"], "duration_ms": value["duration_ms"],
                    "container_id": observed["container_id"],
                    "reason": "verification_pytest_configuration_unproved",
                })
        result = {
            "outcome": outcome,
            "executed": True,
            "passed": outcome == "passed",
            "exit_code": exit_code,
            "stdout": value["stdout"],
            "stderr": value["stderr"],
            "duration_ms": value["duration_ms"],
            "container_id": observed["container_id"],
        }
        if argv[0] == "pytest" or argv[:3] == ["python", "-m", "pytest"]:
            # All probe fields, including its claimed trust flag, are visible
            # to target Python in the same interpreter. Never mint a verdict.
            raise _UnverifiedPytest({**result, "outcome": "unverified", "passed": False,
                "reason": "verification_pytest_independent_observation_unavailable"})
        return result

    async def execute_prepared(self, binding):
        """Owned create/start/inspect/stop lifecycle; never invokes legacy execute."""
        from Sprout.execution.verification_manifest import _VerificationExecution, digest

        if type(binding) is not _VerificationExecution:
            raise PermissionError("verification_execution_binding_required")
        broker = binding.issuer
        record, plan, durable = await broker._verification_execution_record(binding)
        execution_id = str(
            uuid5(
                NAMESPACE_URL,
                binding.scope.task.id
                + ":ordinary:"
                + binding.scope.node.id
                + ":"
                + binding.obligation,
            )
        )
        durable.update(id=execution_id, signature=digest(plan), slot=binding.obligation)
        unknown = {
            "outcome": "UNKNOWN",
            "executed": False,
            "passed": False,
            "execution_id": execution_id,
            "retry_allowed": False,
        }
        record.update(
            execution_id=execution_id, signature=durable["signature"], image=plan["image"],
            container_name=plan["container_name"]
        )
        from Sprout.execution.process_broker import ProcessBroker

        try:
            claimed = await ProcessBroker._verification_wait(record,
                self.metadata.claim_verification_execution(durable), 2)
        except asyncio.CancelledError:
            # A SQLite worker may still commit. Keep custody and storage alive;
            # late success is never permission to continue this cancelled call.
            raise
        except Exception:
            return {**unknown, "reason": "verification_claim_unconfirmed",
                    "manual_intervention_required": True}
        if not claimed:
            return {**unknown, "reason": "verification_claim_unavailable"}
        container_id = None
        result = unknown

        async def step(action):
            return await broker._verification_step(binding, action, container_id)

        async def inspect(*, limits=True):
            response = await step("inspect")
            if not response.allowed or response.exit_code != 0:
                raise ValueError("verification_inspect_unavailable")
            values = json.loads(response.stdout)
            if type(values) is not list or len(values) != 1 or type(values[0]) is not dict:
                raise ValueError("verification_container_inspect_result")
            payload = values[0]
            self.observed_container(plan, payload, expected_id=container_id)
            if limits:
                self.inspect_limits(plan, payload)
            return payload

        async def terminate():
            if container_id is None:
                # A name/label match is not a create response. Never adopt an
                # unknown container or retry create after an ambiguous CLI result.
                return False
            try:
                payload = await inspect(limits=False)
                if payload["State"]["Status"] == "created" and not payload["State"]["Running"]:
                    record["never_started_observed"] = True
                    return False  # Not a stopped/executed result; never start during cleanup.
                if not self.observed_container(plan, payload, expected_id=container_id)[
                    "stopped_observed"
                ]:
                    try:
                        await step("stop")
                    except (OSError, ValueError):
                        pass  # Only a fresh matching inspect can admit one kill.
                    payload = await inspect(limits=False)
                    if not self.observed_container(plan, payload, expected_id=container_id)[
                        "stopped_observed"
                    ]:
                        await step("kill")
                        payload = await inspect(limits=False)
                return self.observed_container(plan, payload, expected_id=container_id)[
                    "stopped_observed"
                ]
            except Exception:
                return False

        async def settle_unknown(reason, *, cancelled=False):
            import time

            # One deadline covers inspect, stop/kill, metadata, CLI spawn,
            # communication and reap. It is never reset by another attempt.
            record.setdefault("cleanup_deadline", time.monotonic() + 20)
            async def job():
                for process in tuple(record.get("cli_processes", {}).values()):
                    try:
                        if process.returncode is None:
                            process.kill()  # exact owned CLI, never a substitute for daemon inspect
                        await ProcessBroker._verification_wait(record, process.wait(), 2)
                        record["cli_processes"].pop(id(process), None)
                    except Exception:
                        record["manual_intervention_required"] = True
                value = {**unknown, "container_id": container_id, "cancelled": cancelled,
                         "stopped_observed": await terminate(), "reason": reason,
                         "steps": list(record.get("steps", ()))}
                record["manual_intervention_required"] = (
                    (container_id is not None or record.get("grant_consumed", False))
                    and not value["stopped_observed"]
                    or any(not t.done() for t in record.get("pending_operations", ()))
                    or any(p.returncode is None for p in record.get("cli_processes", {}).values()))
                value.update(manual_intervention_required=record["manual_intervention_required"],
                    container_name=record.get("container_name"),
                    cleanup_state="stopped_observed" if value["stopped_observed"] else "UNKNOWN")
                try:
                    saved = await ProcessBroker._verification_wait(record,
                        self.metadata.finish_verification_execution(
                            execution_id, durable["owner"], "uncertain", value), 2)
                except Exception:
                    saved = False
                if not saved:
                    value["record_settlement"] = "UNKNOWN"
                    record["manual_intervention_required"] = True
                value["manual_intervention_required"] = record["manual_intervention_required"]
                return value
            helper = asyncio.create_task(job())
            broker._verification_cleanup_task(binding, helper)
            interrupted = cancelled
            try:
                while not helper.done():
                    try:
                        await asyncio.shield(helper)
                    except asyncio.CancelledError:
                        interrupted = True
                return helper.result(), interrupted
            finally:
                record.pop("cleanup_task", None)

        try:
            created = await step("create")
            import re

            if (
                not created.allowed
                or created.exit_code != 0
                or not re.fullmatch(r"[0-9a-f]{64}", created.stdout.strip())
            ):
                raise ValueError("verification_create_unconfirmed")
            container_id = created.stdout.strip()
            record["container_id"] = container_id
            record["create_confirmed"] = True
            plan = {**plan, "container_id": container_id}
            payload = await inspect()
            if payload["State"]["Status"] != "created" or payload["State"]["Running"]:
                raise ValueError("verification_start_state_changed")
            process = await step("start")
            payload = await inspect()
            if not process.allowed or process.exit_code is None:
                raise ValueError("verification_start_unconfirmed")
            try:
                result = self.command_observation(plan, process.stdout, process.exit_code, payload)
            except _UnverifiedPytest as error:
                # Only this owned, started, inspected execution may retain
                # process evidence. The pure parser deliberately still refuses.
                result = error.evidence
            result["cli_process"] = asdict(process)
            result["steps"] = list(record.get("steps", ()))
            record["stopped_observed"] = True
            await binding.scope.runtime._verification_preparation_record(
                binding.manifest,
                broker,
                binding.scope,
                binding.exported,
                allow_expired=True,
            )
            result.update(execution_id=execution_id, retry_allowed=False)
        except asyncio.CancelledError:
            await settle_unknown("cancelled", cancelled=True)
            raise
        except Exception as error:
            result, cancelled = await settle_unknown(type(error).__name__ + ":" + str(error))
            if cancelled:
                raise asyncio.CancelledError from error
            return result
        result["steps"] = list(record.get("steps", ()))
        state = "completed" if result.get("executed") else "uncertain"
        try:
            saved = await ProcessBroker._verification_wait(record,
                self.metadata.finish_verification_execution(
                    execution_id, durable["owner"], state, result), 2)
        except asyncio.CancelledError:
            await settle_unknown("settlement_cancelled", cancelled=True)
            raise
        except Exception:
            saved = False
        if not saved:
            record["manual_intervention_required"] = True
            return {**unknown, "container_id": container_id,
                    "record_settlement": "UNKNOWN", "manual_intervention_required": True}
        return result

    @staticmethod
    def inspect_limits(plan, payload):
        """Daemon-reported mounts/settings must match; CLI flags are not proof."""
        expected = {}
        command = plan["command"]
        for i, arg in enumerate(command):
            if arg == "--mount":
                parts = dict(
                    piece.split("=", 1) if "=" in piece else (piece, True)
                    for piece in command[i + 1].split(",")
                )
                expected[parts["dst"]] = (parts["src"], not parts.get("readonly", False))
        actual = {
            m["Destination"]: (m["Source"], m["RW"])
            for m in payload.get("Mounts", ())
            if m.get("Type") == "bind"
        }
        host = payload.get("HostConfig", {})
        if plan.get("protocol") == "observed-command-v1":
            entry = next(x.split("=", 1)[1] for x in command if x.startswith("--entrypoint="))
            config = payload.get("Config", {})
            if (config.get("Entrypoint") != [entry]
                    or config.get("WorkingDir") != "/workspace"
                    or config.get("Cmd") != list(command[command.index(plan["image"])+1:])):
                raise ValueError("verification_container_command_changed")
        tmpfs = dict(x.split("=", 1)[1].split(":", 1)
                     for x in command if x.startswith("--tmpfs="))
        if any(
            m.get("Type") != "bind"
            and not (m.get("Type") == "tmpfs" and m.get("Destination") in tmpfs)
            for m in payload.get("Mounts", ())
        ):
            raise ValueError("verification_extra_mount")
        for item in payload.get("Config", {}).get("Env", ()):
            name = item.split("=", 1)[0].upper()
            if name != "GPG_KEY" and any(p in name for p in ("KEY", "SECRET", "TOKEN", "PASSWORD")):
                raise ValueError("verification_container_credentials")
        if (
            actual != expected
            or (plan.get("protocol") == "observed-command-v1" and host.get("Tmpfs") != tmpfs)
            or payload.get("Config", {}).get("User") != "65532:65532"
            or host.get("ReadonlyRootfs") is not True
            or host.get("NetworkMode") != "none"
            or host.get("Privileged") is not False
            or host.get("PidsLimit") != 64
            or host.get("Memory") != 512 * 1024 * 1024
            or host.get("MemorySwap") != 512 * 1024 * 1024
            or host.get("NanoCpus") != 1_000_000_000
            or host.get("CapAdd")
            or host.get("GroupAdd")
            or host.get("Devices")
            or "ALL" not in host.get("CapDrop", ())
            or not any(x.startswith("no-new-privileges") for x in host.get("SecurityOpt", ()))
        ):
            raise ValueError("verification_container_limits_or_mounts_changed")

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
