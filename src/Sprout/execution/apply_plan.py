"""Local Receipt → exact text replacements. Private Runtime state is authority.

No Git writes, portable grants, force, rollback, or reconstruction from JSON.
"""

import asyncio
import difflib
import hashlib
import json
import os
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.acceptance import local_executor_applicability
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.execution.models import ApplyResult, ChangeProposal, ChangeProposalStatus, DiffResult
from Sprout.execution.models import TestResult as CheckResult
from Sprout.execution.node_result_source import payload_digest
from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.security.approval import ApprovalStatus
from Sprout.workspace.authorized_content import relative_label
from Sprout.workspace.classifier import classify
from Sprout.workspace.models import ResourceKind, ResourceRef


def sha(data):
    return hashlib.sha256(data).hexdigest()


async def receipt_context(runtime, receipt, principal):
    if os.name != "nt" or getattr(runtime, "_verification_stopping", False):
        raise PermissionError("local_apply_unavailable")
    entry = runtime._local_acceptance_receipts.get(receipt)
    if entry is None or receipt.issuer is not runtime:
        raise PermissionError("local_apply_receipt_unavailable")
    payload, candidate = entry
    if payload_digest(payload) != receipt.payload_sha256:
        raise PermissionError("local_apply_receipt_changed")
    store = runtime._metadata_or_raise()
    task = await store.get_task(receipt.task_id)
    workspace = await store.get_workspace(payload["workspace_id"])
    node = await store.get_execution_node(receipt.node_id)
    if (
        principal is None
        or not principal.authenticated
        or task is None
        or task.actor != principal
        or task.delegation_scope.is_expired()
        or task.status.value in {"cancelled", "failed"}
        or replace(task, status=candidate["task"].status) != candidate["task"]
        or workspace != candidate["workspace"]
        or node is None
        or node.task_id != task.id
        or node.type.value != "evaluation"
        or node.status.value != "completed"
        or node.metadata.get("execution_guard", {}).get("state") != "completed"
        or node.metadata.get("execution_guard", {}).get("owner") != receipt.owner
        or payload_digest(node.metadata.get("output")) != payload["output_sha256"]
        or local_executor_applicability(payload["executor"]) != "match"
        or not payload["session"]
    ):
        raise PermissionError("local_apply_origin_or_owner_changed")
    await runtime._sessions.require_owner(payload["session"], principal)
    return task, workspace, payload, candidate, node.metadata["output"]


async def resolve_resource(runtime, permit):
    from Sprout.tools.context_keys import _current_project_node

    record = runtime._local_apply_permits.get(permit)
    scope = _current_project_node()
    if (
        record is None
        or record["owner"] is not asyncio.current_task()
        or scope is not record["scope"]
        or scope.runtime is not runtime
    ):
        raise PermissionError("local_apply_resource_unregistered")
    task, workspace = await runtime._project_analysis_subject(scope)
    await receipt_context(runtime, record["receipt"], task.actor)
    if task.id != record["task_id"] or workspace != record["workspace"]:
        raise PermissionError("local_apply_resource_subject_changed")
    root, path = record["root"], record["path"]
    action = ActionType.FILE_READ
    if record.get("write"):
        plan = await current_plan(runtime, record["proposal_id"], task.actor)
        row = await runtime._metadata_or_raise().get_apply_transaction(record["transaction_id"])
        approval = await runtime._approvals.store.get_approval(plan["approval_id"])
        if (
            scope.node.type.value != "apply"
            or row is None
            or row["record"]["owner"] != scope.guard_owner
            or row["record"]["authorization_plan_sha256"] != plan["sha256"]
            or row["physical_state"] != "active"
            or row["files"].get(path, {}).get("status") != "WRITE_STARTED"
            or approval is None
            or approval.status is not ApprovalStatus.CONSUMED
            or approval.tool != "file.apply"
            or approval.task_id != task.id
            or approval.requested_by != task.actor.user_id
            or approval.source != task.actor.source
            or approval.session_id != plan["body"]["session_id"]
            or approval.resource_scope != record["proposal_id"]
            or not approval.single_use
            or approval.used_at is None
            or approval.expires_at is not None
            and approval.expires_at <= datetime.now(UTC)
            or approval.arguments_fingerprint != runtime._approvals.fingerprint(arguments(plan))
        ):
            raise PermissionError("local_apply_write_start_or_approval_unproved")
        action = ActionType.FILE_APPLY
    target = root.root / path
    scope_bound = task.delegation_scope.bind_paths(str(root.root))
    request = ActionRequest(
        task_id=task.id,
        actor=task.actor,
        action=action,
        scope=scope_bound,
        resource=ResourceRef(
            workspace.id,
            target.as_posix(),
            kind=classify(
                target, workspace_root=root.root, overrides=runtime._file_broker._classify_overrides
            ),
        ),
        arguments={
            "proposal_id": record.get("proposal_id", ""),
            "plan_sha256": record.get("plan_sha256", ""),
        },
    )
    prior_request = record.get("request")
    if prior_request is None:
        record["request"] = request
    else:
        request = replace(request, id=prior_request.id, created_at=prior_request.created_at)
    if action is ActionType.FILE_APPLY:
        ordinary = replace(request, action=ActionType.FILE_WRITE)
        if (
            not scope_bound.permits("file.write", target.as_posix())
            or runtime._policy_engine.decide(ordinary).decision is AccessDecision.DENY
        ):
            raise PermissionError("local_apply_file_write_denied")
    return (request, root, path, record.get("identity"), record.get("before"), record.get("data"))


async def snapshot(runtime, scope, receipt, workspace, root, path):
    permit = object()
    runtime._local_apply_permits[permit] = dict(
        scope=scope,
        owner=asyncio.current_task(),
        receipt=receipt,
        task_id=scope.task.id,
        workspace=workspace,
        root=root,
        path=path,
    )
    try:
        return await runtime._file_broker._apply_snapshot(permit)
    finally:
        runtime._local_apply_permits.pop(permit, None)


async def publish(runtime, scope):
    from Sprout.execution.host_acceptance import pin_execution_directory

    _, workspace = await runtime._project_analysis_subject(scope)
    with ExitStack() as stack:
        pin = stack.enter_context(pin_execution_directory(workspace.root))
        result = await _publish(runtime, scope, pin)
        plan = runtime._local_apply_plans.get(result.get("proposal_id"))
        if plan is not None and "target_stack" not in plan:
            plan["target_pin"] = pin
            plan["target_stack"] = stack.pop_all()
        return result


def release(runtime):
    for plan in runtime._local_apply_plans.values():
        if "target_stack" in plan:
            plan["target_stack"].close()
    runtime._local_apply_plans.clear()
    runtime._local_apply_permits.clear()


async def _publish(runtime, scope, target_pin):
    """New Local Proposal only; never upgrade an existing partial/review record."""
    store = runtime._metadata_or_raise()
    if scope.node.type.value != "approval":
        raise PermissionError("local_apply_publication_node_required")
    receipts = [
        r
        for r in runtime._local_acceptance_receipts
        if r.task_id == scope.task.id and r.node_id in scope.node.dependencies
    ]
    if len(receipts) != 1:
        raise PermissionError("local_apply_receipt_source_required")
    receipt = receipts[0]
    task, workspace, payload, candidate, evaluation = await receipt_context(
        runtime, receipt, scope.task.actor
    )
    effective = task.delegation_scope.bind_paths(str(workspace.root))
    if not task.delegation_scope.allowed_paths or "file.apply" not in effective.allowed_actions:
        raise PermissionError("local_apply_explicit_scope_required")
    from Sprout.execution.local_git_review import base_versions, review

    git = await review(runtime, scope, receipt)
    bases = await base_versions(runtime, scope)
    unsupported = [{"path": p, "operation": "added"} for p, b in bases.items() if b is None]
    if unsupported:
        return {
            "waiting": True,
            "waiting_kind": "configuration",
            "status": "apply_unsupported",
            "reason": "local_git_unsupported_operations:"
            + ",".join(x["operation"] + ":" + x["path"] for x in unsupported),
            "unsupported_operations": unsupported,
            "git_diff": git,
            "applied": False,
        }
    prior = await store.list_change_proposals(task.id)
    if prior:
        plan = await current_plan(runtime, prior[0].id, task.actor)
        return {
            "proposal_id": prior[0].id,
            "status": prior[0].status.value,
            "waiting": prior[0].status is not ChangeProposalStatus.APPROVED,
            "apply_plan_sha256": plan["sha256"],
        }
    sources = payload["after"]
    if len({x["path"].casefold() for x in sources}) != len(sources):
        raise PermissionError("local_apply_duplicate_inputs")
    files, content_patches = [], []
    protected = set(task.metadata["host_acceptance_v1"]["resources"])
    for item in sources:
        path = relative_label(item["path"])
        if any(part.casefold() in {".git", ".env", ".ssh"} for part in path.split("/")):
            raise PermissionError("local_apply_private_path")
        source = await snapshot(runtime, scope, receipt, workspace, candidate["sandbox"], path)
        if source.digest != item["sha256"] or asdict(source.identity) != item["identity"]:
            raise PermissionError("local_apply_verified_source_changed")
        target = await snapshot(runtime, scope, receipt, workspace, workspace, path)
        # Receipt inputs include unchanged tests/modules, not just write targets.
        if target.data == source.data:
            continue
        if (
            path in protected
            or not path.endswith(".py")
            or classify(workspace.root / path, workspace_root=workspace.root)
            is not ResourceKind.SOURCE
        ):
            raise PermissionError("local_apply_unsupported_file_operation")
        ordinary = ActionRequest(
            task_id=task.id,
            actor=task.actor,
            action=ActionType.FILE_WRITE,
            scope=effective,
            resource=ResourceRef(
                workspace.id, (workspace.root / path).as_posix(), kind=ResourceKind.SOURCE
            ),
        )
        apply = replace(ordinary, action=ActionType.FILE_APPLY)
        if (
            not effective.permits("file.write", ordinary.resource.path)
            or runtime._policy_engine.decide(ordinary).decision is AccessDecision.DENY
            or runtime._policy_engine.decide(apply).decision is AccessDecision.DENY
        ):
            raise PermissionError("local_apply_plan_policy_denied")
        old, new = target.data.decode("utf-8"), source.data.decode("utf-8")
        if "\x00" in old + new:
            raise PermissionError("local_apply_binary_unsupported")
        # This is the exact Workspace effect, distinct from G1's baseline Diff.
        content_patches.append(
            "".join(
                difflib.unified_diff(
                    old.splitlines(True),
                    new.splitlines(True),
                    fromfile="a/" + path,
                    tofile="b/" + path,
                )
            )
        )
        files.append(
            dict(
                path=path,
                identity=asdict(target.identity),
                before=target.digest,
                after=source.digest,
                new_text=new,
            )
        )
    if set(git["changed_paths"]) != {f["path"] for f in files}:
        raise PermissionError("local_git_plan_change_set_mismatch")
    if not files:
        return {"status": "no_changes", "files_changed": []}
    patch = git["patches"]["tracked"] + git["patches"]["untracked"]
    diffs = [DiffResult("authorized_git_view", patch)]
    proposal = ChangeProposal(
        task_id=task.id,
        sandbox_ref=candidate["sandbox"],
        files_changed=tuple(x["path"] for x in files),
        diffs=tuple(diffs),
        risk="high",
        test_results=tuple(
            CheckResult(
                x["name"],
                x["passed"],
                x.get("output", ""),
                x.get("duration_ms", 0),
                x.get("executed", True),
            )
            for x in evaluation["test_results"]
        ),
    )
    body = dict(
        version=1,
        proposal_id=proposal.id,
        task_id=task.id,
        workspace_id=workspace.id,
        target=str(workspace.root),
        target_identity=list(target_pin.identity),
        principal=asdict(task.actor),
        session_id=payload["session"],
        receipt_sha256=receipt.payload_sha256,
        receipt_node_id=receipt.node_id,
        git_review_sha256=git["sha256"],
        git_baseline=git["baseline"],
        git_paths=git["paths"],
        files=files,
        patch="".join(content_patches),
        patch_format="unified_content_diff",
    )
    body = json.loads(json.dumps(body, allow_nan=False))
    digest = fingerprint(body)
    proposal = replace(
        proposal,
        metadata={
            "summary": task.instruction,
            "review_only": False,
            "workspace_id": workspace.id,
            "apply_plan": body,
            "apply_plan_sha256": digest,
            "change_coverage": {
                "status": "complete_for_apply_plan",
                "paths": list(proposal.files_changed),
                "limits": "existing ordinary UTF-8 Python file replacements only",
            },
            "diff_format": "git_no_index_managed_view",
            "git_diff_status": "available",
            "git_diff": git,
            "content_diff": {
                "format": "unified_content_diff",
                "text": "".join(content_patches),
                "sha256": sha("".join(content_patches).encode()),
            },
            "verification": deepcopy(evaluation),
        },
    )
    proposal = replace(
        proposal, metadata=json.loads(json.dumps(proposal.metadata, allow_nan=False))
    )
    await store.save_change_proposal(proposal)
    runtime._local_apply_plans[proposal.id] = {
        "body": deepcopy(body),
        "sha256": digest,
        "receipt": receipt,
        "proposal": proposal,
        "approval_id": "",
        "approval_lock": asyncio.Lock(),
    }
    return {
        "proposal_id": proposal.id,
        "status": "pending",
        "waiting": True,
        "apply_plan_sha256": digest,
        "files_changed": list(proposal.files_changed),
    }


async def current_plan(runtime, proposal_id, principal):
    record = runtime._local_apply_plans.get(proposal_id)
    if record is None:
        raise PermissionError("local_apply_plan_registry_unavailable")
    await receipt_context(runtime, record["receipt"], principal)
    git = runtime._local_git_reviews.get(record["body"]["task_id"], {})
    if git.get("receipt") is not record["receipt"] or git.get("evidence", {}).get(
        "sha256"
    ) != record["body"].get("git_review_sha256"):
        raise PermissionError("local_apply_git_origin_unavailable")
    record["target_pin"].check()
    if list(record["target_pin"].identity) != record["body"]["target_identity"]:
        raise PermissionError("local_apply_target_root_changed")
    proposal = await runtime._metadata_or_raise().get_change_proposal(proposal_id)
    comparable = proposal
    if proposal is not None and proposal.sandbox_ref is not None:
        comparable = replace(
            proposal,
            sandbox_ref=replace(proposal.sandbox_ref, root=Path(proposal.sandbox_ref.root)),
        )
    if (
        proposal is None
        or proposal.metadata.get("review_only") is not False
        or proposal.metadata.get("apply_plan") != record["body"]
        or proposal.metadata.get("apply_plan_sha256") != record["sha256"]
        or fingerprint(record["body"]) != record["sha256"]
        or replace(comparable, status=record["proposal"].status) != record["proposal"]
    ):
        raise PermissionError("local_apply_proposal_or_plan_changed")
    return record


def arguments(plan):
    body = plan["body"]
    return {
        "plan_sha256": plan["sha256"],
        "proposal_id": body["proposal_id"],
        "task_id": body["task_id"],
        "workspace_id": body["workspace_id"],
        "principal": body["principal"],
        "session_id": body["session_id"],
        "files": [{k: v for k, v in f.items() if k != "new_text"} for f in body["files"]],
    }


async def approve(runtime, proposal_id, principal):
    plan = await current_plan(runtime, proposal_id, principal)
    async with plan["approval_lock"]:
        return await _approve(runtime, proposal_id, principal)


async def _approve(runtime, proposal_id, principal):
    plan = await current_plan(runtime, proposal_id, principal)
    store = runtime._metadata_or_raise()
    proposal = await store.get_change_proposal(proposal_id)
    if proposal.status in {ChangeProposalStatus.APPROVED, ChangeProposalStatus.APPLIED}:
        return proposal
    if proposal.status is not ChangeProposalStatus.PENDING:
        raise PermissionError("local_apply_proposal_not_pending")
    if not plan["approval_id"]:
        request = await runtime._approvals.request(
            "file.apply",
            arguments(plan),
            task_id=proposal.task_id,
            requested_by=principal.user_id,
            source=principal.source,
            session_id=plan["body"]["session_id"],
            resource_scope=proposal.id,
            single_use=True,
        )
        plan["approval_id"] = request.id
    record = await runtime._approvals.store.get_approval(plan["approval_id"])
    if (
        record is None
        or record.arguments_fingerprint != runtime._approvals.fingerprint(arguments(plan))
        or record.status is not ApprovalStatus.PENDING
    ):
        raise PermissionError("local_apply_approval_changed")
    await runtime._approvals.decide(
        record.id, True, decided_by=principal.user_id, channel=principal.source
    )
    # Do not overwrite a rejection or changed Patch made while approval awaited.
    await current_plan(runtime, proposal_id, principal)
    snapshot_row = await store.get_git_proposal_snapshot(proposal_id)
    settle = getattr(store, "approve_apply_proposal", None)
    if settle is None or not await settle(snapshot_row):
        raise PermissionError("local_apply_approval_proposal_changed")
    return await store.get_change_proposal(proposal_id)


async def execute(runtime, proposal, workspace, principal):
    from Sprout.execution.file_access import ConditionalWriteRefused
    from Sprout.execution.file_snapshot import ResourceIdentity
    from Sprout.tools.context_keys import _current_project_node

    plan = await current_plan(runtime, proposal.id, principal)
    # Direct Broker callers receive the same canonical Proposal/report gates.
    from Sprout.execution.verification import proposal_verification_blocker

    proposal = await runtime._metadata_or_raise().get_change_proposal(proposal.id)
    if (
        proposal is None
        or proposal_verification_blocker(proposal)
        or proposal.metadata.get("review_only") is not False
        or proposal.metadata.get("change_coverage", {}).get("status") != "complete_for_apply_plan"
    ):
        raise PermissionError("local_apply_verification_incomplete")
    scope = _current_project_node()
    if (
        scope is None
        or scope.runtime is not runtime
        or scope.task.id != proposal.task_id
        or scope.node.type.value != "apply"
        or proposal.status is not ChangeProposalStatus.APPROVED
    ):
        raise PermissionError("local_apply_active_owner_required")
    task, current_workspace = await runtime._project_analysis_subject(scope)
    if workspace.root != current_workspace.root:
        raise PermissionError("local_apply_workspace_changed")
    workspace = current_workspace
    from Sprout.execution.local_git_review import validate

    await validate(runtime, scope, plan["receipt"], proposal.metadata["git_diff"])
    store = runtime._metadata_or_raise()
    transaction = str(uuid5(NAMESPACE_URL, "ordinary-apply:" + proposal.id))
    if await store.get_apply_transaction(transaction) is not None:
        return ApplyResult(proposal.id, False, "local_apply_no_repeat:" + transaction)
    # Revalidate all exact tested inputs before consuming execution approval.
    _, _, payload, candidate, _ = await receipt_context(runtime, plan["receipt"], principal)
    for item in payload["after"]:
        captured = await snapshot(
            runtime, scope, plan["receipt"], workspace, candidate["sandbox"], item["path"]
        )
        if captured.digest != item["sha256"] or asdict(captured.identity) != item["identity"]:
            return ApplyResult(proposal.id, False, "local_apply_verified_source_changed")
    # Check every original before acquiring/consuming any write authority.
    for item in plan["body"]["files"]:
        captured = await snapshot(
            runtime, scope, plan["receipt"], workspace, workspace, item["path"]
        )
        if captured.digest != item["before"] or asdict(captured.identity) != item["identity"]:
            return ApplyResult(proposal.id, False, "local_apply_target_version_changed")
    args = arguments(plan)
    approval = await runtime._approvals.store.get_approval(plan["approval_id"])
    if (
        approval is None
        or approval.status is not ApprovalStatus.APPROVED
        or approval.arguments_fingerprint != runtime._approvals.fingerprint(args)
    ):
        return ApplyResult(proposal.id, False, "local_apply_exact_approval_required")
    compact = {
        "version": 1,
        "files": [{k: v for k, v in f.items() if k != "new_text"} for f in plan["body"]["files"]],
    }
    snapshot_row = await store.get_git_proposal_snapshot(proposal.id)
    record = {
        "version": 2,
        "id": transaction,
        "task_id": task.id,
        "workspace_id": workspace.id,
        "proposal_id": proposal.id,
        "node_id": scope.node.id,
        "owner": scope.guard_owner,
        "principal": asdict(principal),
        "target": str(workspace.root),
        "plan": compact,
        "plan_sha256": fingerprint(compact),
        "authorization_plan": plan["body"],
        "authorization_plan_sha256": plan["sha256"],
        "approval_id": plan["approval_id"],
    }
    if not await store.claim_apply_transaction(record, snapshot_row):
        return ApplyResult(proposal.id, False, "local_apply_claim_refused")
    pending = None
    try:
        if not await runtime._approvals.is_approved(
            "file.apply",
            args,
            task_id=task.id,
            requested_by=principal.user_id,
            source=principal.source,
            session_id=plan["body"]["session_id"],
            resource_scope=proposal.id,
        ):
            await store.update_apply_transaction(
                transaction, scope.guard_owner, reason="exact_approval_not_consumed"
            )
            return ApplyResult(proposal.id, False, "local_apply_exact_approval_required")
        for item in plan["body"]["files"]:
            await current_plan(runtime, proposal.id, principal)
            if not await store.update_apply_transaction(
                transaction, scope.guard_owner, start=item["path"]
            ):
                raise PermissionError("local_apply_write_start_refused")
            pending = item["path"]
            permit = object()
            runtime._local_apply_permits[permit] = dict(
                scope=scope,
                owner=asyncio.current_task(),
                receipt=plan["receipt"],
                task_id=task.id,
                workspace=workspace,
                root=workspace,
                path=pending,
                identity=ResourceIdentity(**item["identity"]),
                before=item["before"],
                data=item["new_text"].encode("utf-8"),
                write=True,
                proposal_id=proposal.id,
                plan_sha256=plan["sha256"],
                transaction_id=transaction,
            )
            try:
                await runtime._file_broker._conditional_apply_write(permit)
                actual = await snapshot(
                    runtime, scope, plan["receipt"], workspace, workspace, pending
                )
                if actual.digest != item["after"]:
                    raise OSError("local_apply_readback_unconfirmed")
            finally:
                runtime._local_apply_permits.pop(permit, None)
            if not await store.update_apply_transaction(
                transaction,
                scope.guard_owner,
                outcome={"path": pending, "status": "WRITTEN", "after": actual.digest},
            ):
                raise OSError("local_apply_observation_not_persisted")
            pending = None
        if not await store.update_apply_transaction(transaction, scope.guard_owner):
            raise OSError("local_apply_settlement_unconfirmed")
        return ApplyResult(proposal.id, True, "COMPLETED:" + transaction)
    except BaseException as exc:
        if pending is not None:
            status = "REJECTED" if isinstance(exc, ConditionalWriteRefused) else "UNKNOWN"
            after = None
            if status == "REJECTED":
                try:
                    actual = await snapshot(
                        runtime, scope, plan["receipt"], workspace, workspace, pending
                    )
                    after = actual.digest
                except Exception:
                    pass
            try:
                await asyncio.shield(
                    store.update_apply_transaction(
                        transaction,
                        scope.guard_owner,
                        outcome={"path": pending, "status": status, "after": after},
                        reason=type(exc).__name__ + ":" + str(exc)[:256],
                    )
                )
            except Exception:
                pass  # Durable WRITE_STARTED is unresolved, never permission to retry.
        try:
            await asyncio.shield(
                store.update_apply_transaction(
                    transaction, scope.guard_owner, reason=type(exc).__name__ + ":" + str(exc)[:256]
                )
            )
        except Exception:
            pass
        if isinstance(exc, asyncio.CancelledError):
            raise
        row = await store.get_apply_transaction(transaction)
        phase = row["phase"] if row else "UNKNOWN"
        return ApplyResult(proposal.id, False, phase + ":" + transaction)
