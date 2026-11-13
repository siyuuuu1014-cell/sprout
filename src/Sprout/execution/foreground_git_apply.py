"""Apply the verified foreground Git candidate through the existing FileBroker ledger."""

import asyncio
from base64 import b64encode
from dataclasses import asdict, replace
from datetime import UTC, datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.file_access import ConditionalWriteRefused
from Sprout.execution.filesystem_apply import _request_approval
from Sprout.execution.git_s1 import _snapshot
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.execution.host_acceptance import pin_execution_directory
from Sprout.execution.post_apply_verification import verify_written_candidate
from Sprout.runtime.filesystem_experiment import FilesystemExperiment
from Sprout.security.access import ActionRequest, ActionType
from Sprout.security.approval import ApprovalStatus
from Sprout.security.local_diagnostics import report
from Sprout.tools.context_keys import _current_project_node
from Sprout.workspace.classifier import classify
from Sprout.workspace.models import ResourceRef


def failed(reason, transaction=None, *, uncertain=False):
    if uncertain:
        return {"applied": False, "waiting": True, "waiting_kind": "resource",
                "error": reason, "transaction_id": transaction}
    return {"applied": False, "git_apply_failed": True, "error": reason,
            "transaction_id": transaction}


async def execute(assembly, node):
    runtime, workspace, store = assembly.runtime, assembly.workspace, assembly.store
    task = await assembly.subject(node)
    scope = task.delegation_scope.bind_paths(str(workspace.root))
    if "file.apply" not in scope.allowed_actions:
        return failed("foreground_git_apply_scope_missing")
    manager, broker = runtime.approvals, runtime._file_broker
    frame = _current_project_node()
    sandbox, review = await assembly.restore_review()
    if assembly.sandbox is not None and assembly.sandbox != sandbox:
        raise PermissionError("foreground_git_review_worktree_changed")
    assembly.sandbox = sandbox
    assembly.check_binding(allow_index_changes=True)
    assembly.provider._check_git_root(sandbox.root)
    digest = sha256(review["diff"].encode("utf-8")).hexdigest()
    transaction = str(uuid5(NAMESPACE_URL, "foreground-git-apply:" + task.id))
    old = await store.get_apply_transaction(transaction)
    if old is not None:
        if (old["phase"] == "COMPLETED" and old.get("verification", {}).get("verified") is True
                and old["record"]["review_diff_sha256"] == digest):
            return {"applied": True, "transaction_id": transaction, "reused": True,
                    "verification": old["verification"]}
        return failed("foreground_git_apply_reconciliation_required", transaction, uncertain=True)
    paths = review["files_changed"]
    # Binding and Broker must both see only the frozen write targets for Apply.
    scope = scope.narrow(replace(scope, allowed_paths=tuple(
        (workspace.root / path).as_posix() for path in paths
    )))
    source, candidate = assembly.bundle["source_files"], review["input_manifest"]
    if any(path not in source or path not in candidate for path in paths):
        return failed("foreground_git_apply_tracked_updates_required")
    await runtime._git_broker._authorize_review(frame, paths, sandbox.root)
    actual_patch = await assembly.provider.review_patch(
        sandbox, review["git_baseline"], tracked=paths, added=(),
    )
    if actual_patch != review["diff"]:
        return failed("foreground_git_apply_review_diff_changed")

    # Reuse the same authorized read boundary as the Filesystem Apply adapter.
    authorize = FilesystemExperiment._authorize_source(assembly, task, workspace)
    with (pin_execution_directory(workspace.root) as original_pin,
          pin_execution_directory(sandbox.root) as candidate_pin):
        def original(path):
            target = workspace.root / path

            def check():
                original_pin.check()
                authorize(target)

            return _snapshot(workspace.root, path, check, 1_000_000)

        before, after, requests = {}, {}, {}
        for path in paths:
            target = workspace.root / path
            if not scope.permits("file.apply", target.as_posix()):
                return failed(f"foreground_git_apply_scope_denied:{path}")
            before[path] = original(path)
            after[path] = _snapshot(sandbox.root, path, candidate_pin.check, 1_000_000)
            if before[path] is None or before[path].digest != source[path]:
                return failed(f"foreground_git_apply_target_conflict:{path}")
            if after[path] is None or after[path].digest != candidate[path]:
                return failed(f"foreground_git_apply_candidate_changed:{path}")
            requests[path] = ActionRequest(
                task_id=task.id, actor=task.actor, action=ActionType.FILE_APPLY, scope=scope,
                resource=ResourceRef(workspace.id, target.as_posix(), kind=classify(
                    target, workspace_root=workspace.root, overrides=broker._classify_overrides)),
            )

        # Acquire no effect authority for a stale independent verification input.
        for path, expected in candidate.items():
            captured = after[path] if path in after else original(path)
            if captured is None or captured.digest != expected:
                return failed(f"foreground_git_apply_verification_input_changed:{path}")

        files = [{"path": path, "identity": asdict(before[path].identity),
                  "before": source[path], "after": candidate[path]} for path in paths]
        arguments = {"task": task.id, "workspace": workspace.id, "transaction": transaction,
                     "bundle_sha256": review["bundle_sha256"], "review_diff_sha256": digest,
                     "verification_sha256": review["verification_sha256"],
                     "git_baseline": review["git_baseline"], "files": files}
        identity = dict(task_id=task.id, requested_by=task.actor.user_id, source=task.source,
                        session_id=task.metadata["session_id"], resource_scope=transaction)
        approval = await _request_approval(
            assembly, task, arguments, identity, review_text=review["diff"], tool="git.apply",
        )
        if approval.status is not ApprovalStatus.APPROVED:
            return failed("foreground_git_apply_rejected")

        plan = {"version": 1, "files": [
            {**item, "before_data": b64encode(before[item["path"]].data).decode("ascii")}
            for item in files
        ]}
        record = {
            **arguments, "version": 4, "id": transaction, "task_id": task.id,
            "proposal_id": "foreground-git:" + task.id, "workspace_id": workspace.id,
            "node_id": node.id, "owner": frame.guard_owner, "target": str(workspace.root),
            "principal": asdict(task.actor), "plan": plan, "plan_sha256": fingerprint(plan),
            "session_id": identity["session_id"], "approval_id": approval.id, "manifest": candidate,
        }
        if not await store.claim_apply_transaction(record, review):
            return failed("foreground_git_apply_claim_refused", transaction)
        report(runtime, "Apply", f"transaction {transaction}: claimed")

        async def write(path, previous, data, *, rollback=False):
            async def resolve():
                await assembly.subject(node)
                original_pin.check()
                row = await store.get_apply_transaction(transaction)
                grant = await manager.store.get_approval(approval.id)
                state = "WRITTEN" if rollback else "WRITE_STARTED"
                if (row is None or row["physical_state"] != "active"
                        or row["record"]["owner"] != frame.guard_owner
                        or row["files"].get(path, {}).get("status") != state
                        or grant is None or grant.status is not ApprovalStatus.CONSUMED
                        or (not rollback and grant.expires_at is not None
                            and grant.expires_at <= datetime.now(UTC))
                        or grant.arguments_fingerprint != manager.fingerprint(arguments)):
                    raise PermissionError("foreground_git_apply_permit_changed")
                return requests[path], workspace, path, previous.identity, previous.digest, data

            permit = object()
            runtime._filesystem_apply_permits[permit] = resolve
            try:
                return await broker._conditional_apply_write(permit)
            finally:
                runtime._filesystem_apply_permits.pop(permit, None)

        pending, written, verification = None, {}, None
        try:
            if not await manager.is_approved("git.apply", arguments, **identity):
                raise PermissionError("foreground_git_apply_approval_unavailable")
            for path in paths:
                assembly.check_binding(allow_index_changes=True)
                if not await store.update_apply_transaction(transaction, frame.guard_owner,
                                                            start=path):
                    raise RuntimeError("foreground_git_apply_write_start_refused")
                pending = path
                result = await write(path, before[path], after[path].data)
                observed = original(path)
                if (observed is None or observed.digest != candidate[path]
                        or result["sha256"] != observed.digest):
                    raise OSError("foreground_git_apply_readback_mismatch")
                if not await store.update_apply_transaction(
                    transaction, frame.guard_owner,
                    outcome={"path": path, "status": "WRITTEN", "after": observed.digest},
                ):
                    raise OSError("foreground_git_apply_observation_not_settled")
                written[path] = observed
                pending = None
                report(runtime, "Apply", f"wrote {path}: {observed.digest}")
            verification = await retest(assembly, task, node, candidate, original)
            if not verification["verified"]:
                raise ValueError("foreground_git_apply_post_test_failed")
            for path, observed in written.items():
                if original(path) != observed:
                    raise OSError(f"foreground_git_apply_readback_changed:{path}")
            if not await store.update_apply_transaction(
                    transaction, frame.guard_owner, verification=verification):
                raise OSError("foreground_git_apply_settlement_unconfirmed")
            return {"applied": True, "transaction_id": transaction, "verification": verification}
        except (Exception, asyncio.CancelledError) as exc:
            reason = str(exc) or type(exc).__name__
            uncertain = pending is not None and not isinstance(exc, ConditionalWriteRefused)
            try:
                if pending is not None:
                    status = "UNKNOWN" if uncertain else "REJECTED"
                    if not await store.update_apply_transaction(
                        transaction, frame.guard_owner, reason=reason,
                        outcome={"path": pending, "status": status, "after": None},
                    ):
                        uncertain = True
                restored = {}
                for path, observed in reversed(tuple(written.items())):
                    result = await write(path, observed, before[path].data, rollback=True)
                    actual = original(path)
                    if (actual is None or actual.digest != before[path].digest
                            or result["sha256"] != actual.digest):
                        raise OSError(f"foreground_git_apply_rollback_unconfirmed:{path}")
                    restored[path] = actual.digest
                if not await store.update_apply_transaction(
                    transaction, frame.guard_owner, reason=reason, verification=verification,
                    **({} if uncertain else {"restored": restored}),
                ):
                    uncertain = True
                if restored:
                    report(runtime, "Apply", f"restored {len(restored)} file(s) after failure")
            except Exception as rollback_error:
                reason += f"; rollback: {rollback_error}"
                uncertain = True
                await store.update_apply_transaction(transaction, frame.guard_owner, reason=reason)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return failed(reason, transaction, uncertain=uncertain)


async def retest(assembly, task, node, expected, capture):
    session = assembly.session(task=task, node=node, sandbox=assembly.sandbox)
    return await session._verified_call(
        verify_written_candidate, runtime=assembly.runtime, session=session,
        expected=expected, capture=capture, acceptance=assembly.acceptance.to_payload(),
        regression=assembly.regression.to_payload(), bundle_sha256=assembly.bundle["bundle_sha256"],
    )
