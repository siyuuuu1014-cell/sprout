"""Policy-controlled subprocess execution (AUTHZ §4.3, §6.1).

Two behaviours changed with the authorization work:

* ``known_command`` is no longer an input. It travelled in the model's tool
  arguments, so the model could attest to itself. The broker now derives it from
  :class:`~Sprout.security.commands.CommandRegistry` by inspecting ``argv[0]``;
  a caller-supplied ``known_command`` is ignored and reported as
  ``auth.self_attestation_ignored``.
* child processes get a **whitelisted** environment instead of inheriting the
  parent's, so an unrelated API key cannot leak into a subprocess. Extra
  variables are injected explicitly by name.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from Sprout.execution.acceptance import (
    local_acceptance_command,
    local_executor_applicability,
    local_executor_binding,
)
from Sprout.execution.models import ProcessResult
from Sprout.execution.policy_emit import emit_policy_decision
from Sprout.execution.proc import OutputLimitExceeded, capture_bounded
from Sprout.execution.proc import terminate as _terminate
from Sprout.gateway.identity import Principal
from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.security.commands import CommandRegistry
from Sprout.security.engine import PolicyEngine
from Sprout.security.secret_broker import SecretBroker
from Sprout.task.models import DelegationScope
from Sprout.tools.context_keys import _cli_process_binding, _consume_cli_approval
from Sprout.workspace.models import ResourceKind, ResourceRef, Workspace

if TYPE_CHECKING:
    from Sprout.events import EventBus
    from Sprout.security.approval import ApprovalManager
    from Sprout.security.audit import SecurityAuditLog

#: Approval key for a withheld command. One grant per task covers its
#: verification commands, so a six-command round asks once rather than six
#: times — the arguments (and so the fingerprint) differ per command, which is
#: what `is_approved` compares.
APPROVAL_TOOL = "process_run"


class ProcessBroker:
    """Runs structured process requests only after a PolicyEngine decision."""

    def __init__(
        self,
        policy: PolicyEngine,
        *,
        default_timeout: float = 60.0,
        commands: CommandRegistry | None = None,
        secrets: SecretBroker | None = None,
        events: EventBus | None = None,
        audit: SecurityAuditLog | None = None,
        extra_env_names: Sequence[str] = (),
        approvals: ApprovalManager | None = None,
    ) -> None:
        self._policy = policy
        self._default_timeout = default_timeout
        self._commands = commands or CommandRegistry()
        self._secrets = secrets or SecretBroker()
        self._events = events
        self._audit = audit
        self._extra_env_names = tuple(extra_env_names)
        # Without a manager, REQUIRE_APPROVAL stays a refusal — the previous
        # behaviour, and the fail-safe default when none is wired.
        self._approvals = approvals
        self._acceptance_context = ContextVar("local_acceptance", default=None)
        self._frozen_dispatch = ContextVar("frozen_execution_dispatch", default=None)

    async def prepare_verification(self, manifest, issuer, scope, exported):
        """P1 preflight only. A valid Manifest is never process permission.

        The image/tool/observer and container termination commissioning required
        for native F3 is deliberately not issued by this interface.
        """
        from copy import deepcopy
        from uuid import uuid4

        from Sprout.evolution import pytest_probe
        from Sprout.execution.verification_backend import VerificationRequest
        from Sprout.execution.verification_manifest import (
            VerificationManifestV1,
            execution_resources,
        )
        from Sprout.execution.verification_run import DockerVerificationRun
        from Sprout.tools.context_keys import _current_project_node
        from Sprout.workspace.models import ResourceRef

        if (type(manifest) is not VerificationManifestV1 or scope is not _current_project_node()
                or scope is None or scope.runtime is not issuer
                or issuer._process_broker is not self):
            raise PermissionError("verification_preflight_frame_denied")
        record = await issuer._verification_preparation_record(manifest, self, scope, exported)
        if record.get("approved_record") is not None:
            arguments = self.verification_approval_arguments(manifest, issuer)
            if record.get("approved_obligation"):
                arguments = self._verification_obligation_arguments(
                    manifest, issuer, record["approved_obligation"])
            await self._check_frozen_approval(record, record["approved_record"],
                arguments, approved=True)
        data = manifest.to_payload()
        task, workspace = record["task"], record["workspace"]
        if (data["task"] != task.id or data["node"] != scope.node.id
                or data["owner"] != scope.guard_owner):
            raise PermissionError("verification_preflight_identity_denied")
        result = {}
        for obligation in data["obligations"]:
            reason = obligation["blocked"]
            outcome = "not_executed"
            if not obligation["targets"] or not set(obligation["targets"]) <= set(data["inputs"]):
                reason = "verification_execution_targets_unavailable"
            decisions = []
            for path in execution_resources(data, obligation):
                sandbox = issuer._frozen_sandboxes[task.id][0]
                target = sandbox.root / path
                request = ActionRequest(task_id=task.id, actor=task.actor,
                    action=ActionType.PROCESS_RUN, scope=task.delegation_scope.bind_paths(
                        str(sandbox.root)), resource=ResourceRef(workspace.id, target.as_posix(),
                        kind=issuer._file_broker._classify(target, sandbox)), arguments={
                        "command": obligation["argv"], "command_origin": "manifest",
                        "verification_manifest_v1": manifest.sha256,
                        "cwd": obligation["cwd"], "image": obligation["image"]})
                decision = self._policy.decide(request)
                if not request.scope.permits("process.run", target.as_posix()):
                    from Sprout.security.access import PolicyDecision

                    decision = PolicyDecision(AccessDecision.DENY, "Verification Scope denied")
                await emit_policy_decision(self._events, request, decision)
                decisions.append(decision.decision.value)
                if decision.decision is AccessDecision.DENY:
                    reason, outcome = decision.reason, "policy_denied"
            prepared = record["plans"].get(obligation["id"])
            if (prepared is None and not reason
                    and obligation["id"] not in {"compileall", "ruff", "demo"}):
                argv = obligation["argv"]
                arguments = argv[3:] if argv[:3] == ["python", "-m", "pytest"] else argv[1:]
                request = VerificationRequest(task.id, workspace.root, Path(exported["root"]),
                    Path(pytest_probe.__file__).resolve(), obligation["image"],
                    ("-", uuid4().hex, *arguments))
                prepared = DockerVerificationRun.prepare(request, node_id=scope.node.id,
                    owner=scope.guard_owner, manifest_sha=manifest.sha256,
                    input_inventory=record["inventories"][exported["root"]])
                if prepared["input_manifest"] != {p: r["digest"]
                                                 for p, r in exported["files"].items()}:
                    raise PermissionError("verification_export_inventory_changed")
            elif (prepared is None and obligation["id"] in {"compileall", "ruff", "demo"}
                  and outcome != "policy_denied"):
                import base64
                import json

                from Sprout.execution import coding_worker

                selected = (exported.get("compileall")
                            if obligation["id"] == "compileall" else exported)
                if selected is not None and obligation["targets"]:
                    if (obligation["id"] == "compileall"
                            and set(selected["files"]) != set(obligation["targets"])):
                        raise PermissionError("verification_compile_selection_changed")
                    encoded = base64.b64encode(json.dumps(obligation["argv"]).encode()).decode()
                    request = VerificationRequest(task.id, workspace.root, Path(selected["root"]),
                        Path(coding_worker.__file__).resolve(), obligation["image"], (encoded,))
                    prepared = DockerVerificationRun.prepare_command(request, node_id=scope.node.id,
                        owner=scope.guard_owner, manifest_sha=manifest.sha256,
                        writable_input=obligation["id"] == "compileall",
                        protected_paths=tuple(data["acceptance"]["resources"]),
                        input_inventory=record["inventories"][selected["root"]])
                    if prepared["input_manifest"] != {p: r["digest"]
                                                     for p, r in selected["files"].items()}:
                        raise PermissionError("verification_command_export_changed")
            if (prepared is not None and obligation["id"] == "compileall"
                    and reason == "verification_disposable_copy_required"
                    and prepared.get("input_mode") == "disposable-copy"
                    and prepared["input_manifest"]
                    == record["inventories"][exported["compileall"]["root"]]):
                reason = ""  # A revalidated cached copy is not a new permission.
            if not reason and not obligation["tool_identity"]:
                reason = "verification_tool_identity_unproved"
            host = issuer._verification_hosts[task.id]
            if (prepared is not None and host.get("docker") is not None and not reason
                    and prepared.get("protocol") != "observed-command-v1"):
                selected = (exported.get("compileall")
                            if obligation["id"] == "compileall" else exported)
                request = VerificationRequest(task.id, workspace.root, Path(selected["root"]),
                    Path(prepared["observer_path"]), obligation["image"], ("placeholder",))
                keys = {"python", obligation["argv"][0]}
                if (obligation["argv"][0] == "pytest"
                        or obligation["argv"][:3] == ["python", "-m", "pytest"]):
                    keys.add("pytest")
                if not keys <= set(host["tools"]):
                    raise PermissionError("verification_tools_unavailable")
                tools = {k: host["tools"][k] for k in keys}
                contract = None
                if "pytest" in keys:
                    discovery = host.get("discovery") or {}
                    contract = {"files": obligation["targets"],
                        "plugins": host["tools"]["pytest"].get("plugins"),
                        "ini_file": discovery.get("pytest_config"),
                        "configs": {p: record["inventories"][selected["root"]][p]
                                    for p in discovery.get("config_files", ())}}
                    if type(contract["plugins"]) is not list:
                        reason = reason or "verification_pytest_configuration_unproved"
                prepared = DockerVerificationRun.prepare_observed(request,
                    logical_argv=obligation["argv"], tools=tools, obligation=obligation["id"],
                    node_id=scope.node.id, owner=scope.guard_owner, manifest_sha=manifest.sha256,
                    input_inventory=record["inventories"][selected["root"]],
                    writable_input=obligation["id"] == "compileall",
                    protected_paths=tuple(data["acceptance"]["resources"]),
                    pytest_contract=contract)
            if prepared is not None:
                record["plans"].setdefault(obligation["id"], deepcopy(prepared))
                for path in execution_resources(data, obligation):
                    sandbox = issuer._frozen_sandboxes[task.id][0]
                    request = ActionRequest(task_id=task.id, actor=task.actor,
                        action=ActionType.PROCESS_RUN,
                        scope=task.delegation_scope.bind_paths(str(sandbox.root)),
                        resource=ResourceRef(workspace.id, (sandbox.root / path).as_posix(),
                            kind=issuer._file_broker._classify(sandbox.root / path, sandbox)),
                        arguments={"command": list(prepared["command"]),
                            "command_origin": "manifest",
                            "verification_manifest_v1": manifest.sha256,
                            "cwd": obligation["cwd"], "image": obligation["image"]})
                    decision = self._policy.decide(request)
                    await emit_policy_decision(self._events, request, decision)
                    decisions.append(decision.decision.value)
                    if (decision.decision is AccessDecision.DENY and outcome != "policy_denied"):
                        reason, outcome = decision.reason, "policy_denied"
            await issuer._verification_preparation_record(manifest, self, scope, exported)
            result[obligation["id"]] = {"outcome": outcome,
                "reason": reason or "verification_native_commissioning_required",
                "policy_decisions": decisions, "prepared": deepcopy(prepared),
                "approval_consumed": False, "started": False, "verified": False}
        return result

    def verification_approval_arguments(self, manifest, issuer):
        from Sprout.execution.verification_manifest import digest

        record = issuer._verification_preparations.get(manifest)
        if record is None or record["manifest_sha"] != manifest.sha256:
            raise PermissionError("verification_approval_plan_unavailable")
        previews = {key: {"argv": value.get("request", {}).get("argv", ()),
            "image": value["image"], "container_name": value["container_name"],
            "cwd": "/workspace", "input_manifest": value["input_manifest"],
            "observer_sha": value["observer_hash"],
            "mounts": [value["command"][i+1] for i, arg in enumerate(value["command"])
                       if arg == "--mount"]} for key, value in record["plans"].items()}
        host = record.get("native", {}).get("host") or issuer._verification_hosts[record["task"].id]
        return {"action": "restricted_verification_v1", "task_id": record["task"].id,
                "manifest": manifest.sha256, "workspace": record["workspace"].id,
                "node": record["scope"].node.id, "owner": record["scope"].guard_owner,
                "execution_plans": digest(record["plans"]), "commands": previews,
                "docker": host.get("docker")}

    async def await_verification_approval(self, manifest, issuer, scope, exported, approval_id,
                                         *, obligation_id=None):
        """No launch or grant consumption. Reissue a fresh key after a real decision.

        P1 EVALUATION does not request/enter this wait while native commissioning
        is absent. Tests drive it with the existing real in-memory manager.
        """
        from datetime import UTC, datetime

        from Sprout.execution.verification_manifest import VerificationManifestV1
        from Sprout.security.approval import ApprovalStatus

        if scope.runtime is not issuer or issuer._process_broker is not self:
            raise PermissionError("verification_approval_issuer_denied")
        record = await issuer._verification_preparation_record(
            manifest, self, scope, exported, allow_expired=True)
        arguments = self.verification_approval_arguments(manifest, issuer)
        if obligation_id is not None:
            if obligation_id not in record["plans"]:
                raise PermissionError("verification_obligation_unavailable")
            arguments = self._verification_obligation_arguments(manifest, issuer, obligation_id)
        node = await issuer._metadata_or_raise().get_execution_node(scope.node.id)
        started = node.metadata.get("execution_guard", {}).get("started_at")
        if not started or node.budget.timeout_seconds <= 0:
            raise PermissionError("verification_approval_budget_unavailable")
        remaining = node.budget.timeout_seconds - (
            datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()
        deadline = time.monotonic() + max(0, remaining - 5)
        while True:
            task, workspace = await issuer._project_analysis_subject(scope)
            if (record["owner"] is not asyncio.current_task() or task != record["task"]
                    or workspace != record["workspace"] or time.monotonic() >= deadline):
                raise PermissionError("verification_approval_authority_or_budget_changed")
            decision = await self._check_frozen_approval(record, approval_id, arguments)
            if decision.status is ApprovalStatus.APPROVED:
                await issuer._verification_preparation_record(
                    manifest, self, scope, exported, allow_expired=True)
                fresh = VerificationManifestV1.from_payload(manifest.to_payload())
                issuer._verification_preparations.pop(manifest, None)
                issuer._verification_preparations[fresh] = {
                    **record, "expires": time.monotonic() + 60,
                    "approved_record": approval_id, "approved_obligation": obligation_id}
                return fresh
            await asyncio.sleep(min(0.2, max(0, deadline - time.monotonic())))

    def _verification_obligation_arguments(self, manifest, issuer, obligation):
        arguments = self.verification_approval_arguments(manifest, issuer)
        preview = arguments["commands"][obligation]
        return {**arguments, "obligation": obligation, "commands": {obligation: preview},
                "0_target": {"task": arguments["task_id"], "node": arguments["node"],
                    "obligation": obligation, "argv": preview["argv"], "image": preview["image"],
                    "cwd": preview["cwd"]}}

    async def execute_verification_obligation(
        self, manifest, issuer, scope, exported, obligation_id, *, wait=False
    ):
        """Same Runtime only. One exact obligation, one existing human grant."""
        from Sprout.execution.verification_manifest import _VerificationExecution
        from Sprout.execution.verification_run import DockerVerificationRun

        prepared = await self.prepare_verification(manifest, issuer, scope, exported)
        row = prepared[obligation_id]
        if (
            row["reason"] != "verification_native_commissioning_required"
            or not row["prepared"]
            or row["prepared"].get("protocol") != "observed-command-v1"
        ):
            return {
                "outcome": row["outcome"],
                "executed": False,
                "passed": False,
                "reason": row["reason"],
            }
        record = await issuer._verification_preparation_record(manifest, self, scope, exported)
        from uuid import NAMESPACE_URL, uuid5

        execution_id = str(uuid5(NAMESPACE_URL, record["task"].id + ":ordinary:"
            + scope.node.id + ":" + obligation_id))
        if await issuer._metadata_or_raise().get_verification_execution(execution_id):
            return {"outcome": "UNKNOWN", "executed": False, "passed": False,
                    "execution_id": execution_id, "reason": "verification_already_claimed",
                    "retry_allowed": False}
        arguments = self._verification_obligation_arguments(manifest, issuer, obligation_id)
        if self._approvals is None:
            return {"outcome": "approval_required", "executed": False, "passed": False}
        from Sprout.security.approval import ApprovalStatus

        approval = await self._approvals.find_pending(APPROVAL_TOOL, arguments,
            task_id=record["task"].id)
        if approval is None:
            approval = await self._approvals.store.find_approval(APPROVAL_TOOL,
                self._approvals.fingerprint(arguments), ApprovalStatus.APPROVED, record["task"].id)
        approval = approval or await self._approvals.request(
            APPROVAL_TOOL,
            arguments,
            task_id=record["task"].id,
            source=record["task"].actor.source,
            requested_by=record["task"].actor.user_id,
            resource_scope=record["workspace"].id,
            session_id=scope.session_id,
        )
        if not wait:
            return {
                "outcome": "approval_required",
                "approval_id": approval.id,
                "executed": False,
                "passed": False,
            }
        fresh = await self.await_verification_approval(
            manifest, issuer, scope, exported, approval.id, obligation_id=obligation_id
        )
        binding = _VerificationExecution(
            self, scope, fresh, exported, obligation_id, approval.id, asyncio.current_task()
        )
        from copy import deepcopy

        host = issuer._verification_hosts[record["task"].id]
        record["native"] = {
            "binding": binding,
            "grant_consumed": False,
            "container_id": None,
            "host": deepcopy(
                {
                    k: host[k]
                    for k in ("docker", "images", "tools", "discovery", "parent", "parent_identity")
                }
            ),
            "protected_roots": (str(record["workspace"].root),
                manifest.to_payload()["sandbox"]["root"], str(host["parent"])),
        }
        issuer._verification_preparations[fresh]["native"] = record["native"]
        try:
            runner = DockerVerificationRun(issuer._metadata_or_raise(), None)
            return await runner.execute_prepared(binding)
        finally:
            issuer._release_verification_preparation(fresh)

    @staticmethod
    async def _verification_wait(native, operation, seconds, *, spawn=False):
        """Bound this F3 await without pretending cancelled I/O has ended.

        Pending underlying operations retain custody and keep storage alive.
        This helper never issues process permission or a Docker command.
        """
        import inspect

        deadline = native.get("cleanup_deadline")
        if deadline is not None:
            seconds = min(seconds, deadline - time.monotonic())
        if seconds <= 0:
            if inspect.iscoroutine(operation):
                operation.close()
            native["manual_intervention_required"] = True
            raise TimeoutError("verification_cleanup_budget_expired")
        task = asyncio.ensure_future(operation)
        pending = native.setdefault("pending_operations", set())
        pending.add(task)
        ticket = {"abandoned": False}

        def finished(done):
            pending.discard(done)
            if done.cancelled():
                return
            error = done.exception()  # retrieve late failures, not a success assertion
            if spawn and error is None:
                process = done.result()
                handles = native.setdefault("cli_processes", {})
                if not ticket.get("registered"):
                    handles[id(process)] = process
                    ticket["registered"] = True
                if ticket["abandoned"] and not ticket.get("reaping"):
                    ticket["reaping"] = True
                    # A delayed spawn still belongs to us. Only kill this CLI;
                    # its exit says nothing about the daemon-side container.
                    try:
                        if process.returncode is None:
                            process.kill()
                        waiter = asyncio.create_task(process.wait())
                        pending.add(waiter)
                        def reaped(value):
                            pending.discard(value)
                            if not value.cancelled() and value.exception() is None:
                                handles.pop(id(process), None)
                        waiter.add_done_callback(reaped)
                    except Exception:
                        native["manual_intervention_required"] = True

        task.add_done_callback(finished)
        try:
            done, _ = await asyncio.wait({task}, timeout=seconds)
            if not done:
                raise TimeoutError("verification_operation_unresolved")
            return task.result()
        except BaseException:
            ticket["abandoned"] = True
            native["manual_intervention_required"] = True
            if spawn and task.done():
                finished(task)  # completion/cancellation can cross in the same loop turn
            # Do not cancel a SQLite worker or an in-flight spawn and mistake
            # cancellation of the awaiter for termination of the operation.
            raise

    async def _verification_execution_record(self, binding, *, cleanup=False):
        import hashlib
        from copy import deepcopy
        from dataclasses import asdict

        from Sprout.execution.docker_verification import safe_path
        from Sprout.execution.verification_manifest import _VerificationExecution

        if type(binding) is not _VerificationExecution or binding.issuer is not self:
            raise PermissionError("verification_native_frame_unavailable")
        issuer, scope = binding.scope.runtime, binding.scope
        if cleanup:
            record = issuer._verification_preparations.get(binding.manifest)
            native = record.get("native", {}) if record else {}
            if (
                record is None
                or record["owner"] is not binding.owner
                or record["scope"] is not scope
                or native.get("binding") is not binding
                or asyncio.current_task() not in {binding.owner, native.get("cleanup_task")}
                or not native.get("grant_consumed")
                or not native.get("container_id")
                or not native.get("create_confirmed")
            ):
                raise PermissionError("verification_cleanup_frame_unavailable")
            durable = await self._verification_wait(native,
                issuer._metadata_or_raise().get_verification_execution(native["execution_id"]), 2)
            if (
                durable is None
                or durable.get("owner") != scope.guard_owner
                or durable.get("task_id") != record["task"].id
                or durable.get("node_id") != scope.node.id
                or durable.get("manifest") != binding.manifest.sha256
                or durable.get("signature") != native["signature"]
                or durable.get("image") != native["image"]
                or durable.get("docker") != native["host"]["docker"]
                or durable.get("approval_id") != binding.approval_id
                or durable.get("state") not in {"active", "completed", "uncertain"}
            ):
                raise PermissionError("verification_cleanup_custody_unproved")
            if time.monotonic() >= native.get("cleanup_deadline", float("inf")):
                raise PermissionError("verification_cleanup_budget_expired")
        else:
            if binding.owner is not asyncio.current_task():
                raise PermissionError("verification_native_call_copied")
            record = await issuer._verification_preparation_record(
                binding.manifest, self, scope, binding.exported
            )
        native = record.get("native", {})
        if native.get("binding") is not binding:
            raise PermissionError("verification_native_binding_unavailable")
        host = native["host"] if cleanup else issuer._verification_hosts[record["task"].id]
        from Sprout.execution.verification_manifest import digest

        if record["host_execution_sha"] != digest(
            {k: host.get(k) for k in ("docker", "images", "tools", "discovery")}
        ):
            raise PermissionError("verification_native_host_changed")
        docker = host.get("docker")
        if not isinstance(docker, dict) or set(docker) != {
            "path",
            "sha256",
            "context",
            "daemon_id",
            "endpoint",
        }:
            raise PermissionError("verification_docker_identity_unavailable")
        import re

        if (any(type(value) is not str for value in docker.values())
                or not Path(docker["path"]).is_absolute()):
            raise PermissionError("verification_docker_binding_invalid")
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", docker["context"])
            or not isinstance(docker["daemon_id"], str)
            or not docker["daemon_id"]
            or not docker["endpoint"].startswith(("npipe://", "unix://"))
        ):
            raise PermissionError("verification_daemon_binding_unavailable")
        executable = safe_path(Path(docker["path"]))
        if any(executable.is_relative_to(Path(root)) for root in native["protected_roots"]):
            raise PermissionError("verification_docker_is_project_resource")
        def executable_digest():
            with executable.open("rb") as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest()
        actual_digest = (await self._verification_wait(native,
            asyncio.to_thread(executable_digest), 2) if cleanup else executable_digest())
        if actual_digest != docker["sha256"]:
            raise PermissionError("verification_docker_identity_changed")
        plan = deepcopy(record["plans"][binding.obligation])
        plan["command"] = (str(executable), *plan["command"][1:])
        task, workspace = record["task"], record["workspace"]
        import json

        persisted_scope = json.loads(json.dumps({"version": 1, **asdict(task.delegation_scope)},
            default=lambda value: sorted(value) if isinstance(value, (set, frozenset))
            else value.isoformat()))
        node_metadata = (
            record["node_metadata"]
            if cleanup
            else (await issuer._metadata_or_raise().get_execution_node(scope.node.id)).metadata
        )
        durable = {
            "schema_version": 3,
            "task_id": task.id,
            "node_id": scope.node.id,
            "workspace_id": workspace.id,
            "owner": scope.guard_owner,
            "nonce": plan["nonce"],
            "manifest": binding.manifest.sha256,
            "container_name": plan["container_name"],
            "image": plan["image"], "docker": deepcopy(docker), "approval_id": binding.approval_id,
            "command": plan["command"],
            "logical_argv": plan["request"]["argv"],
            "authority": {
                "actor": asdict(task.actor),
                "scope": persisted_scope,
                "task_metadata": task.metadata,
                "node_metadata": node_metadata,
                "workspace_root": str(workspace.root),
                "workspace_revision": workspace.revision,
                "instruction": task.instruction,
            },
        }
        return native, plan, durable

    def _verification_cleanup_task(self, binding, task):
        """Only the owning call can appoint one bounded stop-only cleanup task."""
        if binding.owner is not asyncio.current_task() or binding.issuer is not self:
            raise PermissionError("verification_cleanup_issuer_denied")
        record = binding.scope.runtime._verification_preparations.get(binding.manifest)
        if not record or record.get("native", {}).get("binding") is not binding:
            raise PermissionError("verification_cleanup_binding_denied")
        record["native"]["cleanup_task"] = task

    async def _verification_step(self, binding, action, container_id):
        """No arbitrary command/cwd/env input. Ordinary run() is unchanged."""
        native, plan, _ = await self._verification_execution_record(
            binding, cleanup=action in {"inspect", "stop", "kill"}
        )
        issuer, scope = binding.scope.runtime, binding.scope
        record = issuer._verification_preparations[binding.manifest]
        cleanup = action in {"inspect", "stop", "kill"}
        host = native["host"] if cleanup else issuer._verification_hosts[record["task"].id]
        docker = host["docker"]
        prefix = (plan["command"][0], "--context=" + docker["context"])
        if action == "create":
            if native["grant_consumed"] or container_id is not None:
                raise PermissionError("verification_create_not_repeatable")
            command = (*prefix, "create", *plan["command"][2:])
        else:
            if not container_id or native["container_id"] != container_id:
                raise PermissionError("verification_container_not_owned")
            suffix = {
                "inspect": ("inspect", container_id),
                "start": ("start", "--attach", container_id),
                "stop": ("stop", "--time=2", container_id),
                "kill": ("kill", container_id),
            }
            command = (*prefix, *suffix[action])
        arguments = self._verification_obligation_arguments(binding.manifest, issuer,
                                                            binding.obligation)
        if not cleanup:
            await self._check_frozen_approval(
                record,
                binding.approval_id,
                arguments,
                approved=True,
                consumed=native["grant_consumed"],
            )
        task = record["task"]
        sandbox = issuer._frozen_sandboxes[task.id][0]
        obligation = next(
            x for x in binding.manifest.to_payload()["obligations"] if x["id"] == binding.obligation
        )
        from Sprout.execution.verification_manifest import execution_resources

        requests = []
        resources = (() if cleanup else
                     execution_resources(binding.manifest.to_payload(), obligation))
        for path in resources:
            target = sandbox.root / path
            request = ActionRequest(
                task_id=task.id,
                actor=task.actor,
                action=ActionType.PROCESS_RUN,
                scope=task.delegation_scope.bind_paths(str(sandbox.root)),
                resource=ResourceRef(
                    record["workspace"].id,
                    target.as_posix(),
                    kind=issuer._file_broker._classify(target, sandbox),
                ),
                arguments={
                    "command": command,
                    "cwd": "/workspace",
                    "verification_manifest_v1": binding.manifest.sha256,
                    "command_origin": "manifest",
                },
            )
            decision = self._policy.decide(request)
            requests.append(request)
            await emit_policy_decision(self._events, request, decision)
            if decision.decision not in {
                AccessDecision.ALLOW,
                AccessDecision.REQUIRE_APPROVAL,
            } or not request.scope.permits("process.run", target.as_posix()):
                raise PermissionError("verification_process_authorization_denied")
        if action in {"create", "start"}:
            await issuer._verification_preparation_record(
                binding.manifest, self, scope, binding.exported
            )
        env = self._secrets.child_env(base_env=os.environ)
        env = {
            k: v
            for k, v in env.items()
            if not any(x in k.upper() for x in ("KEY", "SECRET", "TOKEN", "PASSWORD"))
        }

        async def capture(argv, seconds):
            from dataclasses import asdict

            from Sprout.execution.host_acceptance import pin_execution_directory

            if not cleanup:
                from datetime import UTC, datetime

                current, workspace = await issuer._project_analysis_subject(scope)
                if current != record["task"] or workspace != record["workspace"]:
                    raise PermissionError("verification_driver_subject_changed")
                for original in requests:
                    request = replace(original, arguments={**original.arguments, "command": argv})
                    decision = self._policy.decide(request)
                    await emit_policy_decision(self._events, request, decision)
                    if self._audit is not None:
                        self._audit.record_decision(request, decision)
                    if (decision.decision not in {
                            AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}
                            or not request.scope.permits("process.run", request.resource.path)):
                        raise PermissionError("verification_command_policy_denied")
                elapsed = (datetime.now(UTC) - datetime.fromisoformat(
                    record["node_metadata"]["execution_guard"]["started_at"])).total_seconds()
                remaining = record["node_budget"].timeout_seconds - elapsed
                if elapsed < 0 or remaining <= 0:
                    raise PermissionError("verification_node_budget_expired")
                seconds = min(seconds, remaining)
            if cleanup and native.get("cleanup_deadline") is not None:
                seconds = min(seconds, native["cleanup_deadline"] - time.monotonic())
                if seconds <= 0:
                    raise PermissionError("verification_cleanup_budget_expired")
            step_deadline = time.monotonic() + seconds
            async def spawn():
                # Keep the pinned directory alive until the underlying spawn
                # really returns, including after its caller's budget expires.
                with pin_execution_directory(host["parent"]) as parent_pin:
                    if parent_pin.identity != host["parent_identity"]:
                        raise PermissionError("verification_driver_cwd_changed")
                    parent_pin.check()
                    return await asyncio.create_subprocess_exec(
                        *argv, **parent_pin.spawn, env=env,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            process = await self._verification_wait(native, spawn(),
                step_deadline - time.monotonic(), spawn=True)
            started = time.monotonic()
            try:
                out, err = await self._verification_wait(native,
                    capture_bounded(process, 600_000), step_deadline - time.monotonic())
                native["cli_processes"].pop(id(process), None)
                result = ProcessResult(
                    argv,
                    process.returncode,
                    self._secrets.redact(out.decode("utf-8", errors="replace")),
                    self._secrets.redact(err.decode("utf-8", errors="replace")),
                    (time.monotonic() - started) * 1000,
                    started=True,
                )
                native.setdefault("steps", []).append(asdict(result))
                return result
            except BaseException:
                native.setdefault("steps", []).append({"command": list(argv), "state": "UNKNOWN"})
                if process.returncode is None:
                    process.kill()  # Only CLI custody, not proof of container stop.
                await self._verification_wait(native, process.wait(),
                    min(3, step_deadline - time.monotonic()))
                native["cli_processes"].pop(id(process), None)
                raise

        endpoint = await capture(
            (
                *prefix,
                "context",
                "inspect",
                "--format",
                "{{json .Endpoints.docker.Host}}",
                docker["context"],
            ),
            2,
        )
        daemon = await capture((*prefix, "info", "--format", "{{json .ID}}"), 2)
        import json

        if (
            endpoint.exit_code != 0
            or daemon.exit_code != 0
            or json.loads(endpoint.stdout) != docker["endpoint"]
            or json.loads(daemon.stdout) != docker["daemon_id"]
        ):
            raise PermissionError("verification_daemon_identity_changed")
        if cleanup:
            from Sprout.execution.verification_run import DockerVerificationRun

            inspected = await capture((*prefix, "inspect", container_id), 2)
            if not inspected.allowed or inspected.exit_code != 0:
                raise PermissionError("verification_cleanup_inspect_unavailable")
            values = json.loads(inspected.stdout)
            if type(values) is not list or len(values) != 1 or type(values[0]) is not dict:
                raise PermissionError("verification_cleanup_inspect_invalid")
            payload = values[0]
            DockerVerificationRun.observed_container(plan, payload, expected_id=container_id)
            if action == "kill" and not native.get("stop_attempted"):
                raise PermissionError("verification_kill_without_stop")
            if action == "stop":
                native["stop_attempted"] = True
            if action == "inspect":
                return inspected
            if self._audit is not None:
                self._audit.record("process.verification_cleanup", {"task_id": task.id,
                    "node_id": scope.node.id, "guard_owner": scope.guard_owner,
                    "container_id": container_id, "manifest": binding.manifest.sha256,
                    "daemon_id": docker["daemon_id"], "action": action,
                    "principal": {"user_id": task.actor.user_id, "source": task.actor.source}})
        if not cleanup:
            await issuer._verification_preparation_record(binding.manifest, self, scope,
                                                         binding.exported)
            if action == "create":
                if not await self._approvals.is_approved(APPROVAL_TOOL, arguments,
                    task_id=task.id, session_id=scope.session_id, requested_by=task.actor.user_id,
                    source=task.actor.source, resource_scope=task.workspace_id):
                    raise PermissionError("verification_grant_unavailable")
                native["grant_consumed"] = True
                await issuer._verification_preparation_record(binding.manifest, self, scope,
                                                             binding.exported)
        return await capture(command, 55 if action == "start" else 3)

    async def run_local_acceptance(
        self, workspace, spec, path, case_id, frozen, *, execution_contract=None, **kwargs,
    ) -> ProcessResult:
        """Mint identity inside the broker, never honor a caller's claimed ID.

        A frozen value is a restriction, not authority. This entry builds its
        own command and still traverses the unchanged policy/approval boundary.
        """
        if local_executor_applicability(frozen) != "match":
            return ProcessResult(command=(), allowed=False, started=False,
                                 error="Local acceptance executor unknown or mismatched")
        binding = local_executor_binding()
        command = local_acceptance_command(spec, path, case_id, binding)
        token = self._acceptance_context.set((command, binding))
        try:
            # A CLI tool's upstream approval is never an Acceptance binding grant.
            kwargs.pop("upstream_approval_granted", None)
            if execution_contract is not None:
                return await self._run_frozen_contract(
                    execution_contract, workspace, spec, path, case_id, binding, command, kwargs)
            return await self.run(workspace, command, **kwargs)
        finally:
            self._acceptance_context.reset(token)

    async def _run_frozen_contract(self, permit, workspace, spec, path, case_id,
                                   binding, command, kwargs):
        from Sprout.execution.host_acceptance import (
            _FrozenExecutionContract,
            pin_execution_directory,
        )
        from Sprout.runtime.runtime import Runtime
        from Sprout.task.paths import scope_path
        from Sprout.tools.context_keys import _current_project_node

        result = None
        token = None
        issuer = None
        try:
            scope = _current_project_node()
            if (type(permit) is not _FrozenExecutionContract or scope is None
                    or not isinstance(scope.runtime, Runtime)
                    or permit.issuer is not scope.runtime):
                raise PermissionError("frozen_execution_contract_unavailable")
            issuer = permit.issuer
            record = issuer._frozen_execution_record(permit, self, claim=True)
            task, sandbox = record["task"], record["sandbox"]
            if (workspace.id != record["workspace"].id or workspace.root != record["workspace"].root
                    or workspace.kind != record["workspace"].kind
                    or spec.to_payload() != record["spec"].to_payload()
                    or path != record["path"] or case_id != record["case_id"]
                    or binding != record["executor"]
                    or scope_path(str(kwargs.get("cwd"))) != scope_path(str(sandbox.root))
                    or kwargs.get("task_id") != task.id or kwargs.get("actor") != task.actor
                    or kwargs.get("scope") != task.delegation_scope
                    or kwargs.get("source") != task.source
                    or kwargs.get("requested_by") != task.actor.user_id
                    or kwargs.get("resource_scope") != task.workspace_id
                    or kwargs.get("session_id", "") != record["scope"].session_id
                    or kwargs.get("command_origin", "manifest") != "manifest"
                    or any(kwargs.get(k) for k in ("env", "secret_names", "required_env",
                                                   "known_command"))):
                raise PermissionError("frozen_execution_parameters_changed")
            timeout = kwargs.get("timeout_seconds")
            limit = kwargs.get("max_output_bytes")
            if ((timeout is not None and (type(timeout) not in {int, float}
                                         or not 0 < timeout <= 60))
                    or (limit is not None and (type(limit) is not int or not 0 < limit <= 262144))):
                raise PermissionError("frozen_execution_budget_changed")
            if self._frozen_dispatch.get() is not None:
                raise PermissionError("frozen_execution_reentrant")
            with pin_execution_directory(sandbox.root) as pin:
                dispatch = SimpleNamespace(permit=permit, record=record, pin=pin,
                    issuer=issuer, command=command, entered=False, owner=asyncio.current_task())
                token = self._frozen_dispatch.set(dispatch)
                kwargs["cwd"] = sandbox.root
                kwargs.setdefault("command_origin", "manifest")
                kwargs["timeout_seconds"] = timeout if timeout is not None else min(
                    self._default_timeout, 60)
                kwargs["max_output_bytes"] = limit if limit is not None else 256 * 1024
                result = await self.run(workspace, command, **kwargs)
                if result.started:
                    try:
                        await issuer._refresh_frozen_execution(permit, self, pin)
                        pin.check()
                    except (OSError, ValueError, PermissionError):
                        result = replace(result, exit_code=None, stdout="", stderr="",
                                         error="Frozen authority/inputs changed; unconfirmed")
                return result
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if result is not None and result.started:
                return replace(result, exit_code=None, stdout="", stderr="",
                               error="Frozen execution result unconfirmed")
            return ProcessResult(command=command, allowed=False, started=False,
                                 error=f"Frozen execution refused: {exc}")
        finally:
            if token is not None:
                self._frozen_dispatch.reset(token)
            if issuer is not None:
                issuer._frozen_executions.pop(permit, None)

    @property
    def commands(self) -> CommandRegistry:
        return self._commands

    async def run(
        self,
        workspace: Workspace,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        preflight_only: bool = False,
        known_command: bool = False,
        task_id: str = "",
        session_id: str = "",
        secret_names: Sequence[str] = (),
        required_env: Sequence[str] = (),
        scope: DelegationScope | None = None,
        command_origin: str = "caller",
        source: str = "interactive",
        requested_by: str = "system",
        actor: Principal | None = None,
        resource_scope: str = "",
        upstream_approval_granted: bool = False,
    ) -> ProcessResult:
        if max_output_bytes is not None and (
            type(max_output_bytes) is not int or not 0 < max_output_bytes <= 8 * 1024 * 1024
        ):
            raise ValueError("Capture limit must be between 1 byte and 8 MiB")
        command_tuple = tuple(str(part) for part in command)
        context = self._acceptance_context.get()
        if context is not None and (
            command_tuple != context[0] or local_executor_applicability(context[1]) != "match"
        ):
            return ProcessResult(command=command_tuple, allowed=False, started=False,
                                 error="Local acceptance executor changed")
        cwd_path = Path(cwd or workspace.root)
        resource = ResourceRef(
            workspace_id=workspace.id,
            path=str(cwd_path),
            kind=ResourceKind.CONFIG,
        )
        if known_command:
            # Caller-supplied attestation is not evidence; the allowlist is.
            await self._notify_self_attestation(command_tuple, task_id)
        tags = self._commands.tag(command_tuple)
        request = ActionRequest(
            task_id=task_id,
            actor=actor if actor is not None else Principal(user_id="system"),
            action=ActionType.PROCESS_RUN,
            resource=resource,
            arguments={
                "command": list(command_tuple),
                # Where the command came from. ``manifest`` means the workspace
                # scanner inferred it, which is the case worth auditing: those
                # run inside the directory the task just wrote to.
                "command_origin": command_origin,
                **tags,
            },
            scope=scope if scope is not None else DelegationScope(),
        )
        dispatch = self._frozen_dispatch.get()
        if dispatch is not None:
            if (dispatch.entered or dispatch.owner is not asyncio.current_task()
                    or context is None or command_tuple != dispatch.command
                    or cwd_path != dispatch.record["sandbox"].root):
                return ProcessResult(command=command_tuple, allowed=False, started=False,
                                     error="Frozen dispatch cannot authorize a plain/repeated call")
            dispatch.entered = True
            try:
                target_request = await dispatch.issuer._refresh_frozen_execution(
                    dispatch.permit, self, dispatch.pin)
            except (OSError, ValueError) as exc:
                return ProcessResult(command=command_tuple, allowed=False, started=False,
                    error="Frozen execution revalidation denied: " + (
                        str(exc) if type(exc) is PermissionError else type(exc).__name__))
            request = replace(target_request, arguments={**request.arguments, "cwd": str(cwd_path),
                "frozen_execution_v1": dispatch.record["approval_binding"]})
        decision = self._policy.decide(request)
        await emit_policy_decision(self._events, request, decision)
        allowlisted = bool(tags["allowlisted_command"])
        self._record_allowlist(command_tuple, allowlisted, command_origin, task_id)
        if dispatch is not None:
            # The event handler may have awaited. Check before approvals as well.
            try:
                await dispatch.issuer._refresh_frozen_execution(dispatch.permit, self, dispatch.pin)
                if dispatch.record.get("approved_record") is not None:
                    await self._check_frozen_approval(
                        dispatch.record, dispatch.record["approved_record"],
                        self._frozen_approval_arguments(dispatch.record), approved=True,
                    )
            except (OSError, ValueError):
                return ProcessResult(command=command_tuple, allowed=False, started=False,
                                     error="Frozen execution authority changed before approval")
        if decision.decision is not AccessDecision.ALLOW:
            # Only the exact plain CLI execution shape can consume a CLI grant;
            # extra secret injection, alternate scope/actor or preflight cannot.
            cli_binding = None
            if (not secret_names and not required_env and scope is None and actor is None
                    and not known_command and command_origin == "caller"
                    and not preflight_only and max_output_bytes is None
                    and Path(workspace.root).resolve() == cwd_path.resolve()):
                cli_binding = _cli_process_binding(
                    command_tuple, cwd_path, env, timeout_seconds,
                    task_id, session_id, source, requested_by, resource_scope,
                    workspace.id, workspace.kind.value,
                )
            blocked = await self._withheld(
                command_tuple,
                decision,
                request,
                resource_scope=resource_scope,
                task_id=task_id,
                session_id=session_id,
                requested_by=requested_by,
                source=source,
                allowlisted=allowlisted,
                upstream_approval_granted=upstream_approval_granted,
                cli_binding=cli_binding,
            )
            if blocked is not None:
                return blocked
            # An existing grant covers it: fall through and run.

        if preflight_only:
            return ProcessResult(command=command_tuple, allowed=True, allowlisted=allowlisted,
                                 started=False)
        started = time.perf_counter()
        process_env = self._secrets.child_env(
            base_env=os.environ,
            required=(*required_env, *self._extra_env_names),
            extra=env,
            secret_names=secret_names,
        )
        spawn_location = {"cwd": str(cwd_path)}
        if dispatch is not None:
            try:
                await dispatch.issuer._refresh_frozen_execution(
                    dispatch.permit, self, dispatch.pin)
                dispatch.pin.check()
                final = self._policy.decide(request)
                if final.decision != decision.decision or final.decision not in {
                        AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}:
                    raise PermissionError("Frozen process policy changed")
                if local_executor_applicability(context[1]) != "match":
                    raise PermissionError("Frozen executor changed")
            except (OSError, ValueError):
                return ProcessResult(command=command_tuple, allowed=False, started=False,
                                     error="Frozen execution pre-spawn revalidation denied")
            import shlex

            process_env = self._secrets.child_env(base_env=os.environ, extra={
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONIOENCODING": "utf-8",
                "PYTEST_ADDOPTS": shlex.join(["--noconftest", "-c", os.devnull,
                    "--rootdir", str(cwd_path), "-p", "no:cacheprovider"]),
            })
            for name in tuple(process_env):
                if any(part in name.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                    process_env.pop(name)
            spawn_location = dispatch.pin.spawn
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command_tuple,
                **spawn_location,
                env=process_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate() if max_output_bytes is None
                else capture_bounded(process, max_output_bytes),
                timeout=timeout_seconds or self._default_timeout,
            )
        except (TimeoutError, OutputLimitExceeded) as exc:
            if process is not None:
                await _terminate(process)
            return ProcessResult(
                command=command_tuple,
                exit_code=None,
                duration_ms=(time.perf_counter() - started) * 1000,
                allowed=True,
                error="Process output limit exceeded" if isinstance(exc, OutputLimitExceeded)
                else "Process timed out",
                allowlisted=allowlisted,
            )
        except asyncio.CancelledError:
            if process is not None:
                await _terminate(process)
            raise
        except OSError as exc:
            return ProcessResult(
                command=command_tuple,
                exit_code=None,
                duration_ms=(time.perf_counter() - started) * 1000,
                allowed=True,
                error=f"{type(exc).__name__}: {exc}",
                allowlisted=allowlisted,
            )
        except BaseException:
            if dispatch is not None and process is not None:
                await _terminate(process)
            raise

        stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
        # Redaction is unconditional: patterns catch credentials that were never
        # declared as secrets and therefore never entered the injection list.
        stdout_result = self._secrets.redact_result(stdout_text, secret_names)
        stderr_result = self._secrets.redact_result(stderr_text, secret_names)
        return ProcessResult(
            command=command_tuple,
            exit_code=process.returncode,
            stdout=stdout_result.text,
            stderr=stderr_result.text,
            duration_ms=(time.perf_counter() - started) * 1000,
            allowed=True,
            allowlisted=allowlisted,
            redactions=stdout_result.count + stderr_result.count,
            started=True,
        )

    async def _withheld(
        self,
        command: tuple[str, ...],
        decision: Any,
        request: ActionRequest,
        *,
        resource_scope: str,
        task_id: str,
        session_id: str,
        requested_by: str,
        source: str,
        allowlisted: bool,
        upstream_approval_granted: bool = False,
        cli_binding: tuple | None = None,
    ) -> ProcessResult | None:
        """Handle a non-ALLOW decision: park on approval, refuse, or proceed.

        Returns ``None`` when an existing grant covers the command, meaning the
        caller should run it. This used to return ``allowed=False`` for every
        non-ALLOW verdict including REQUIRE_APPROVAL — so verification commands
        were refused outright and *nobody was ever asked*. Every EVALUATION
        round reported failure while executing nothing, and a change could
        reach approval with no test having run.

        Now the broker asks, mirroring ``ToolExecutor``. The security posture
        is unchanged: approval is still required, which is the point, since a
        worktree sandbox is a logical boundary rather than an OS one and these
        commands execute code the task just wrote.
        """
        withheld = ProcessResult(
            command=command,
            allowed=False,
            error=decision.reason,
            allowlisted=allowlisted,
            started=False,
        )
        if decision.decision is not AccessDecision.REQUIRE_APPROVAL:
            return withheld
        # The legacy bool is attribution only, never approval evidence. DENY
        # was handled above. Source policy precedes any upstream handoff.
        context = self._acceptance_context.get()
        if self._approvals is not None and not self._approvals.asks_a_human(source):
            # Unattended source: no human decision can arrive. Check before
            # consuming any CLI handoff, including otherwise valid ones.
            return replace(withheld, error=f"{decision.reason} (source {source!r} is unattended, "
                           f"mode: {self._approvals.mode_for(source).value})")
        if context is None and _consume_cli_approval(self, self._approvals, cli_binding):
            return None
        if self._approvals is None:
            return replace(
                withheld,
                error=f"{decision.reason} (no approval manager is configured)",
            )
        # Task-scoped, not command-scoped: the approval arguments deliberately
        # carry no command, so one grant covers a whole verification round.
        # Keyed per command, a six-command round would ask six times, which no
        # one would use — and `is_approved` compares a fingerprint of these
        # arguments, so omitting the command is what makes one grant reusable.
        approval_arguments = {
            "action": "verification_commands",
            "task_id": task_id,
            "resource_scope": resource_scope,
        }
        if context is not None:
            approval_arguments["local_acceptance_executor_v1"] = context[1]
        dispatch = self._frozen_dispatch.get()
        if dispatch is not None:
            approval_arguments["frozen_execution_v1"] = dispatch.record["approval_binding"]
        if dispatch is not None and dispatch.record.get("approved_record") is not None:
            try:
                await self._check_frozen_approval(
                    dispatch.record, dispatch.record["approved_record"], approval_arguments,
                    approved=True,
                )
            except (ValueError, PermissionError) as exc:
                return replace(withheld, error=str(exc))
        if await self._approvals.is_approved(
            APPROVAL_TOOL,
            approval_arguments,
            task_id=task_id,
            session_id=session_id,
            requested_by=requested_by,
            source=source,
            resource_scope=resource_scope,
        ):
            return None  # granted: run it
        existing = await self._approvals.find_pending(
            APPROVAL_TOOL, approval_arguments, task_id=task_id
        )
        if existing is not None:
            # A round runs its commands concurrently, so without this each one
            # would raise its own request and the operator would face five
            # identical prompts for one decision.
            return replace(withheld, approval_id=existing.id)
        record = await self._approvals.request(
            APPROVAL_TOOL,
            approval_arguments,
            task_id=task_id,
            requested_by=requested_by,
            resource_scope=resource_scope,
            source=source,
            session_id=session_id,
            # Reusable so the rest of the round lands on the same grant; still
            # bounded by task_id and expiry.
            single_use=False,
        )
        return replace(withheld, approval_id=record.id)

    def _frozen_approval_arguments(self, record):
        return {"action": "verification_commands", "task_id": record["task"].id,
                "resource_scope": record["workspace"].id,
                "local_acceptance_executor_v1": record["executor"],
                "frozen_execution_v1": record["approval_binding"]}

    async def _check_frozen_approval(self, execution, approval_id, arguments, *, approved=False,
                                     consumed=False):
        from datetime import UTC, datetime

        from Sprout.security.approval import ApprovalStatus
        from Sprout.task.models import coerce_source

        if self._approvals is None:
            raise PermissionError("frozen_approval_manager_unavailable")
        record = await self._approvals.store.get_approval(approval_id)
        task, scope = execution["task"], execution["scope"]
        if (record is None or record.tool != APPROVAL_TOOL or record.task_id != task.id
                or record.source != coerce_source(task.actor.source).value
                or record.session_id != scope.session_id
                or record.requested_by != (task.actor.user_id or "system")
                or record.resource_scope != task.workspace_id
                or record.arguments_fingerprint != self._approvals.fingerprint(arguments)
                or record.action_hash != self._approvals.fingerprint(
                    {"tool": APPROVAL_TOOL, **arguments})):
            raise PermissionError("frozen_approval_identity_mismatch")
        if record.expires_at is None or record.expires_at <= datetime.now(UTC):
            raise PermissionError("frozen_approval_expired")
        valid = {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}
        if consumed:
            valid = {ApprovalStatus.CONSUMED, ApprovalStatus.APPROVED}
        if record.status not in valid:
            raise PermissionError(f"frozen_approval_{record.status.value}")
        if record.status in {ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED}:
            if not record.decided_by or record.decided_at is None:
                raise PermissionError("frozen_approval_decision_unavailable")
        elif approved:
            raise PermissionError("frozen_approval_not_approved")
        return record

    async def _wait_frozen_approval(self, execution, approval_id):
        """Wait in the original node; retained data is not an execution permit."""
        from datetime import UTC, datetime

        from Sprout.security.approval import ApprovalStatus

        scope = execution["scope"]
        runtime = scope.runtime
        arguments = self._frozen_approval_arguments(execution)
        started = scope.node.metadata.get("execution_guard", {}).get("started_at")
        if not started or scope.node.budget.timeout_seconds <= 0:
            raise PermissionError("frozen_approval_node_budget_unavailable")
        remaining = scope.node.budget.timeout_seconds - (
            datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()
        deadline = time.monotonic() + max(0, remaining - 5)
        while True:
            task, workspace = await runtime._project_analysis_subject(scope)
            if (asyncio.current_task() is not execution["owner"]
                    or task != execution["task"] or workspace != execution["workspace"]):
                raise PermissionError("frozen_approval_authority_changed")
            if time.monotonic() >= deadline:
                raise PermissionError("frozen_approval_wait_timeout")
            record = await self._check_frozen_approval(execution, approval_id, arguments)
            if record.status is ApprovalStatus.APPROVED:
                return
            await asyncio.sleep(min(0.5, max(0, deadline - time.monotonic())))

    def _record_allowlist(
        self,
        command: tuple[str, ...],
        allowlisted: bool,
        command_origin: str,
        task_id: str,
    ) -> None:
        """Audit the allowlist verdict (AUTHZ §7.4).

        ``command.allowlisted`` is declared ``bus_event=False`` in the catalog:
        it belongs on the security audit stream, not the event bus. Recording
        never raises into the caller's path.
        """
        if self._audit is None:
            return
        from Sprout.security.audit import KIND_COMMAND_ALLOWLISTED

        self._audit.record(
            KIND_COMMAND_ALLOWLISTED,
            {
                "command": list(command),
                "argv0": command[0] if command else "",
                "allowlisted": allowlisted,
                "command_origin": command_origin,
                "task_id": task_id,
            },
        )

    async def _notify_self_attestation(
        self, command: tuple[str, ...], task_id: str
    ) -> None:
        if self._events is None:
            return
        from Sprout.events import AUTH_SELF_ATTESTATION_IGNORED, Event

        await self._events.publish(
            Event(
                AUTH_SELF_ATTESTATION_IGNORED,
                {"command": list(command), "task_id": task_id},
            )
        )
