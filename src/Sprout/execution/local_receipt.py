"""Runtime-only Windows Local receipt. Serialized output is never a credential."""

import os
from copy import deepcopy
from dataclasses import asdict, dataclass, replace

from Sprout.execution.acceptance import local_executor_applicability
from Sprout.execution.node_result_source import payload_digest


@dataclass(frozen=True, eq=False, slots=True)
class LocalAcceptanceReceiptV1:
    issuer: object
    task_id: str
    node_id: str
    owner: str
    payload_sha256: str
    version: int = 1


def check_results(planned, output, executor):
    if (
        not planned
        or len(set((k, tuple(v)) for k, v in planned)) != len(planned)
        or output.get("waiting")
        or output.get("pending_approvals")
        or local_executor_applicability(executor) != "match"
    ):
        raise PermissionError("local_receipt_checks_unproved")
    records = output.get("results")
    if not isinstance(records, list) or len(records) != len(planned):
        raise PermissionError("local_receipt_checks_missing")
    names = set()
    for (kind, command), record in zip(planned, records, strict=True):
        if (
            record.get("kind") != kind
            or not command
            or record.get("name") in names
            or record.get("name") != f"{kind}: {' '.join(command)}"
            or record.get("outcome") != "passed"
            or record.get("passed") is not True
            or record.get("allowed") is not True
            or record.get("started") is not True
            or type(record.get("exit_code")) is not int
            or record.get("exit_code") != 0
            or record.get("error")
            or record.get("approval_id")
        ):
            raise PermissionError("local_receipt_check_failed")
        names.add(record.get("name"))
    return deepcopy(records)


async def settle(runtime):
    """Called after actual orchestration settlement, never at node return."""
    if os.name != "nt":
        return
    for key, candidate in tuple(getattr(runtime, "_local_receipt_candidates", {}).items()):
        metadata = runtime._metadata_or_raise()
        node = await metadata.get_execution_node(key[1])
        task = await metadata.get_task(key[0])
        workspace = await metadata.get_workspace(candidate["workspace"].id)
        guard = node.metadata.get("execution_guard", {}) if node else {}
        if node is None or node.status.value != "completed" or guard.get("state") != "completed":
            continue
        if (
            guard.get("owner") != candidate["owner"]
            or node.task_id != key[0]
            or node.id != key[1]
            or node.type.value != "evaluation"
            or task is None
            or task.status.value in {"cancelled", "failed"}
            or replace(task, status=candidate["task"].status) != candidate["task"]
            or workspace != candidate["workspace"]
            or task.delegation_scope.is_expired()
            or payload_digest(node.metadata.get("output")) != candidate["output_sha256"]
            or local_executor_applicability(candidate["executor"]) != "match"
        ):
            runtime._local_receipt_candidates.pop(key, None)
            candidate["stack"].close()
            continue
        # Recheck retained native pins after Guard settlement; candidate bytes
        # were captured while these handles prevented write/delete sharing.
        try:
            if candidate["session"]:
                await runtime._sessions.require_owner(candidate["session"], task.actor)
            candidate["pin"].check()
        except (OSError, ValueError, PermissionError):
            runtime._local_receipt_candidates.pop(key, None)
            candidate["stack"].close()
            continue
        payload = {
            k: deepcopy(candidate[k])
            for k in ("owner", "session", "spec_sha256", "executor", "checks", "before", "after")
        }
        payload.update(
            guard_state="completed",
            node_status="completed",
            output_sha256=candidate["output_sha256"],
            acceptance_spec=deepcopy(task.metadata["host_acceptance_v1"]["spec"]),
            task_id=task.id,
            workspace_id=workspace.id,
            node_id=node.id,
            principal=asdict(task.actor),
            sandbox={**asdict(candidate["sandbox"]), "root": str(candidate["sandbox"].root)},
        )
        digest = payload_digest(payload)
        receipt = LocalAcceptanceReceiptV1(runtime, task.id, node.id, candidate["owner"], digest)
        runtime._local_acceptance_receipts[receipt] = (payload, candidate)
        runtime._local_receipt_candidates.pop(key, None)
        candidate["stack"].close()  # Custody ends after completed-Guard issuance.


async def evaluate(runtime, scope, run):
    """Pin exact declared inputs across all checks and completed-Guard wait."""
    from contextlib import ExitStack

    from Sprout.execution.host_acceptance import pin_execution_directory
    from Sprout.tools.context_keys import _current_project_node

    if os.name != "nt" or scope is not _current_project_node():
        return await run()
    task, workspace = await runtime._project_analysis_subject(scope)
    if task.id in runtime._verification_hosts:
        return await run()  # F3 Docker does not acquire Local trust.
    registered = runtime._frozen_sandboxes.get(task.id)
    if not registered or "host_acceptance_v1" not in task.metadata:
        return await run()
    sandbox = registered[0]
    paths = task.metadata.get("required_paths")
    if not isinstance(paths, (list, tuple)) or not paths or len(paths) > 64:
        return await run()
    stack = ExitStack()
    try:
        pin = stack.enter_context(pin_execution_directory(sandbox.root))
        before = []
        for path in paths:
            try:
                pin.pin_file(path)
            except FileNotFoundError:
                local_git = getattr(runtime, "_local_git_reviews", {}).get(task.id)
                if local_git is None or local_git.get("source") is None:
                    raise
                # Before run(): no acceptance process has started. Do not claim
                # a Git rename from a missing input, or issue a Receipt for it.
                return {
                    "waiting": True,
                    "waiting_kind": "configuration",
                    "status": "unverified",
                    "verified": False,
                    "reason": "local_git_unsupported_missing_input:" + path,
                    "unsupported_operations": [
                        {
                            "path": path,
                            "operation": "missing_input",
                            "limits": "deletion/rename/unmaterialized input not supported",
                        }
                    ],
                    "git_diff": {"status": "not_executed", "reason": "required_source_missing"},
                }
            snapshot = await runtime._file_broker._local_receipt_snapshot(
                scope, sandbox, path, pin_stack=stack
            )
            before.append(snapshot)
        output = await run()
        after = [
            await runtime._file_broker._local_receipt_snapshot(scope, sandbox, path)
            for path in paths
        ]
        pin.check()
        if before != after:
            raise PermissionError("local_receipt_source_changed")
        planned = runtime._local_receipt_plans.pop((task.id, scope.node.id), None)
        executor = output.get("local_executor")
        try:
            checks = check_results(planned, output, executor)
        except PermissionError:
            return output  # A real failed evaluation is not a successful receipt.
        spec = task.metadata["host_acceptance_v1"]["spec"]
        runtime._local_receipt_candidates[(task.id, scope.node.id)] = {
            "task": task,
            "workspace": workspace,
            "owner": scope.guard_owner,
            "session": scope.session_id,
            "spec_sha256": payload_digest(spec),
            "executor": deepcopy(executor),
            "checks": checks,
            "before": before,
            "after": after,
            "sandbox": sandbox,
            "pin": pin,
            "stack": stack,
            "output_sha256": payload_digest(output),
        }
        stack = None
        return output
    finally:
        if stack is not None:
            stack.close()


def release(runtime):
    """Revoke credentials before dropping their native custody."""
    candidates = list(getattr(runtime, "_local_receipt_candidates", {}).values())
    candidates += [
        entry[1] for entry in getattr(runtime, "_local_acceptance_receipts", {}).values()
    ]
    getattr(runtime, "_local_receipt_candidates", {}).clear()
    getattr(runtime, "_local_acceptance_receipts", {}).clear()
    getattr(runtime, "_local_receipt_plans", {}).clear()
    for candidate in candidates:
        candidate["stack"].close()
