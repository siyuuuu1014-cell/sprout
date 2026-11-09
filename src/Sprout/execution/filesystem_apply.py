"""Filesystem adapter for the shared Apply effect ledger and FileBroker permit."""

import asyncio
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.file_access import ConditionalWriteRefused
from Sprout.execution.file_snapshot import ResourceIdentity
from Sprout.execution.filesystem_review import render_review
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.execution.host_acceptance import pin_execution_directory
from Sprout.sandbox.filesystem import capture_snapshot, checked_label
from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.security.approval import ApprovalManager, ApprovalStatus
from Sprout.security.local_diagnostics import DiagnosticApprovals, report
from Sprout.tools.context_keys import _current_project_node
from Sprout.workspace.classifier import classify
from Sprout.workspace.models import ResourceRef


def withheld(reason, transaction=None, *, uncertain=False):
    return {"waiting": True, "waiting_kind": "resource", "error": reason,
            "transaction_id": transaction, "filesystem_prewrite": not uncertain}


async def _request_approval(experiment, task, arguments, identity, *, review_text=""):
    """Request a single-use approval for the exact prepared file plan."""
    runtime = experiment.runtime
    manager = runtime.approvals
    session_id = identity["session_id"]
    if isinstance(manager, DiagnosticApprovals):
        mode = manager.diagnostics
        await mode.require()
        if (mode.runtime is not runtime or mode.session_id != session_id
                or mode.principal != task.actor):
            raise PermissionError("filesystem_apply_diagnostic_subject_changed")
        approval = await ApprovalManager.request(
            manager, "filesystem.apply", arguments, request_approval=False,
            ttl_seconds=60, **identity)
        mode.records.add(approval.id)
        approved = True
    else:
        approval = await manager.request("filesystem.apply", arguments, **identity)
        approved = await experiment.confirm_apply(arguments, review_text)
    return await manager.decide(approval.id, approved, principal=task.actor,
                                decided_by=task.actor.user_id, channel="cli")


async def execute(experiment, evidence, payload):
    runtime = experiment.runtime
    if os.name != "nt":
        return withheld("filesystem_apply_platform_unavailable")
    frame = _current_project_node()
    task, workspace = await runtime._project_analysis_subject(frame)
    if (experiment.task.id != task.id or experiment.workspace != workspace
            or asyncio.current_task() is not experiment.owner
            or runtime._sandbox_lifecycle.filesystem.get(task.id) is not experiment):
        raise PermissionError("filesystem_apply_live_owner_required")
    session_id = task.metadata["session_id"]
    owned = await runtime._sessions.require_owner(session_id, task.actor)
    if (runtime._sessions.frozen_principal(owned) != task.actor
            or runtime.session_workspace_id(owned) != workspace.id):
        raise PermissionError("filesystem_apply_session_changed")

    store, broker, manager = runtime._metadata_or_raise(), runtime._file_broker, runtime.approvals
    digest = evidence["fingerprint"]
    source = f"filesystem:{task.id}:{digest}"
    transaction = str(uuid5(NAMESPACE_URL, source))
    old = await store.get_apply_transaction(transaction)
    if old is not None:
        if old["phase"] == "COMPLETED":
            return {"applied": True, "transaction_id": transaction, "reused": True}
        return withheld("filesystem_apply_reconciliation_required", transaction,
                        uncertain=old["phase"] in {"WRITE_STARTED", "PARTIAL", "UNKNOWN"})

    scope = task.delegation_scope.bind_paths(str(workspace.root))
    versions = dict(payload["sources"])
    requests, changes, files = {}, {}, []
    for change in payload["changes"]:
        label = checked_label(change["path"], workspace.root, broker._classify_overrides)
        runtime._check_cli_write_target(task, label)
        target = workspace.root / label
        request = ActionRequest(
            task_id=task.id, actor=task.actor, action=ActionType.FILE_APPLY, scope=scope,
            resource=ResourceRef(workspace.id, target.as_posix(), kind=classify(
                target, workspace_root=workspace.root, overrides=broker._classify_overrides)),
        )
        if (not scope.permits("file.apply", target.as_posix())
                or not scope.permits("file.write", target.as_posix())
                or runtime._policy_engine.decide(request).decision not in {
                    AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}
                or runtime._policy_engine.decide(
                    replace(request, action=ActionType.FILE_WRITE)
                ).decision is AccessDecision.DENY):
            return withheld("filesystem_apply_policy_denied")
        before = change["before"]
        files.append({"path": label, "identity": before["identity"] if before else None,
                      "before": before["sha256"] if before else None,
                      "after": change["after"]["sha256"]})
        versions[label] = before
        requests[label], changes[label] = request, change

    plan = {"version": 1, "files": files}
    arguments = {"action": "filesystem.apply", "task": task.id, "workspace": workspace.id,
                 "changeset": digest, "transaction": transaction, "files": files}
    identity = dict(task_id=task.id, requested_by=task.actor.user_id, source=task.source,
                    session_id=session_id, resource_scope=transaction)
    authorize = experiment._authorize_source(task, workspace)

    with pin_execution_directory(workspace.root) as pin:
        if list(pin.identity) != list(payload["workspace_identity"]):
            return withheld("filesystem_apply_workspace_changed")

        def check_source(label, expected):
            target = workspace.root / label

            def check():
                pin.check()
                authorize(target)

            snapshot = capture_snapshot(workspace.root, target, check,
                                        limit=0 if expected is None else 256 * 1024)
            if expected is None:
                return snapshot.reason == "snapshot_not_found"
            return (snapshot.ok and snapshot.digest == expected["sha256"]
                    and asdict(snapshot.identity) == expected["identity"])

        for label, expected in versions.items():
            if not check_source(label, expected):
                return withheld(f"filesystem_apply_source_conflict:{label}")

        review_text = render_review(evidence, task.id, workspace.id, verification_status="passed")
        approval = await _request_approval(
            experiment, task, arguments, identity, review_text=review_text)
        if approval.status is not ApprovalStatus.APPROVED:
            return withheld("filesystem_apply_rejected")

        record = {"version": 3, "id": transaction, "proposal_id": source, "task_id": task.id,
                  "workspace_id": workspace.id, "node_id": frame.node.id,
                  "owner": frame.guard_owner, "target": str(workspace.root),
                  "principal": asdict(task.actor), "plan": plan, "plan_sha256": fingerprint(plan),
                  "session_id": session_id, "approval_id": approval.id}
        if not await store.claim_apply_transaction(record, digest):
            return withheld("filesystem_apply_claim_refused", transaction)
        report(runtime, "Apply", f"transaction {transaction}: claimed")

        pending = None
        try:
            if not await manager.is_approved("filesystem.apply", arguments, **identity):
                await store.update_apply_transaction(
                    transaction, frame.guard_owner, reason="exact_approval_not_consumed")
                return withheld("filesystem_apply_approval_unavailable", transaction)
            for label, expected in versions.items():
                if not check_source(label, expected):
                    reason = f"filesystem_apply_source_conflict:{label}"
                    await store.update_apply_transaction(
                        transaction, frame.guard_owner, reason=reason)
                    return withheld(reason, transaction)
            for item in files:
                label = item["path"]
                for source_path, expected in versions.items():
                    if not check_source(source_path, expected):
                        raise ConditionalWriteRefused(
                            f"filesystem_apply_source_conflict:{source_path}")
                if not await store.update_apply_transaction(
                        transaction, frame.guard_owner, start=label):
                    raise RuntimeError("filesystem_apply_write_start_refused")
                pending = label

                async def resolve(label=label, item=item):
                    await runtime._project_analysis_subject(frame)
                    pin.check()
                    row = await store.get_apply_transaction(transaction)
                    grant = await manager.store.get_approval(approval.id)
                    if (row is None or row["physical_state"] != "active"
                            or row["record"]["owner"] != frame.guard_owner
                            or row["files"][label]["status"] != "WRITE_STARTED"
                            or grant is None or grant.status is not ApprovalStatus.CONSUMED
                            or (grant.expires_at is not None
                                and grant.expires_at <= datetime.now(UTC))
                            or grant.arguments_fingerprint != manager.fingerprint(arguments)):
                        raise PermissionError("filesystem_apply_permit_changed")
                    before = ResourceIdentity(**item["identity"]) if item["identity"] else None
                    return (requests[label], workspace, label, before, item["before"],
                            changes[label]["after"]["content"].encode("utf-8"))

                permit = object()
                runtime._filesystem_apply_permits[permit] = resolve
                try:
                    result = await broker._conditional_apply_write(permit)
                finally:
                    runtime._filesystem_apply_permits.pop(permit, None)
                if not await store.update_apply_transaction(
                    transaction, frame.guard_owner,
                    outcome={"path": label, "status": "WRITTEN", "after": result["sha256"]},
                ):
                    raise OSError("filesystem_apply_readback_not_settled")
                pending = None
                # This path has now intentionally changed, so it is no longer
                # an unchanged-source precondition for subsequent writes.
                versions.pop(label)
                operation = "updated" if item["identity"] else "created"
                report(runtime, "Apply", f"file {operation} and verified: {label}")
            if not await store.update_apply_transaction(transaction, frame.guard_owner):
                raise OSError("filesystem_apply_settlement_unconfirmed")
            report(runtime, "Apply", f"verified {len(files)} file(s)")
            return {"applied": True, "transaction_id": transaction, "files": len(files)}
        except BaseException as exc:
            reason = f"{type(exc).__name__}:{str(exc)[:160]}"
            try:
                if pending is not None:
                    status = "REJECTED" if isinstance(exc, ConditionalWriteRefused) else "UNKNOWN"
                    await asyncio.shield(store.update_apply_transaction(
                        transaction, frame.guard_owner,
                        outcome={"path": pending, "status": status, "after": None},
                        reason=reason))
                await asyncio.shield(store.update_apply_transaction(
                    transaction, frame.guard_owner, reason=reason))
            except Exception:
                pass  # A durable WRITE_STARTED claim remains non-replayable.
            if not isinstance(exc, Exception):
                raise
            row = await store.get_apply_transaction(transaction)
            phase = row["phase"] if row else "UNKNOWN"
            return withheld(f"filesystem_apply_{phase.lower()}:{reason}", transaction,
                            uncertain=phase in {"WRITE_STARTED", "PARTIAL", "UNKNOWN"})


async def transaction_summary(store, task_id, changeset):
    transaction = str(uuid5(NAMESPACE_URL, f"filesystem:{task_id}:{changeset}"))
    row = await store.get_apply_transaction(transaction)
    if row is None or row["record"]["task_id"] != task_id:
        return ""
    lines = [f"Apply transaction: {transaction}", f"Phase: {row['phase']}"]
    lines.append(f"Approval record: {row['record']['approval_id']}")
    for item in row["record"]["plan"]["files"]:
        path = item["path"]
        observed = row["files"].get(path, {})
        lines.append(f"    {path}: {observed.get('status', 'PREPARED')}")
    if row["reason"]:
        lines.append(f"    Reason: {row['reason']}")
    if row["phase"] in {"WRITE_STARTED", "PARTIAL", "UNKNOWN"}:
        lines.append("Reconciliation required. No automatic replay.")
    return "\n\n".join(lines[:3]) + "\n\n" + "\n".join(lines[3:])
