"""Local Receipt adapter to the existing G1 issuer. No F3 Host reconstruction."""

import json
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from Sprout.execution import git_s1
from Sprout.execution.host_acceptance import binding_subject, pin_execution_directory
from Sprout.execution.node_result_source import payload_digest
from Sprout.execution.verification_manifest import digest, scope_digest
from Sprout.tools.result import ToolExecutionUnconfirmed
from Sprout.workspace.authorized_content import relative_label


async def configure(
    runtime, task_id, principal, *, executable, executable_sha256, export_parent, chain=()
):
    task = await runtime._metadata_or_raise().get_task(task_id)
    if (
        getattr(runtime, "_verification_stopping", False)
        or task is None
        or principal is None
        or not principal.authenticated
        or task.actor != principal
        or task.source != principal.source
        or task.status.value != "created"
        or task.execution_mode != "code"
        or "host_acceptance_v1" not in task.metadata
        or task_id in runtime._verification_hosts
        or task_id in runtime._local_git_reviews
        or task.delegation_scope.is_expired()
        or not task.delegation_scope.allowed_paths
        or not {"git.diff", "file.read", "file.apply"}.issubset(
            task.delegation_scope.allowed_actions
        )
        or await runtime._metadata_or_raise().list_execution_nodes(task_id)
    ):
        raise PermissionError("local_git_configuration_subject_denied")
    workspace = await runtime._metadata_or_raise().get_workspace(task.workspace_id)
    paths = tuple(relative_label(p) for p in task.metadata.get("required_paths", ()))
    if not 0 < len(paths) <= 64 or len(set(p.casefold() for p in paths)) != len(paths):
        raise PermissionError("local_git_selection_required")
    executable, parent = (
        Path(executable).resolve(strict=True),
        Path(export_parent).resolve(strict=True),
    )
    if (
        not executable.is_absolute()
        or executable.name.casefold() not in {"git", "git.exe"}
        or not re.fullmatch("[0-9a-f]{64}", executable_sha256)
        or not parent.is_absolute()
        or type(chain) is not tuple
        or len(chain) > 8
        or any(not Path(p).is_absolute() or not re.fullmatch("[0-9a-f]{64}", h) for p, h in chain)
    ):
        raise PermissionError("local_git_installation_required")
    runtime._git_broker._check_review_policy(task, paths, workspace.root)
    with pin_execution_directory(parent) as pin:
        state = {
            "task": deepcopy(task),
            "workspace": deepcopy(workspace),
            "paths": paths,
            "parent": parent,
            "parent_identity": pin.identity,
            "executable": executable,
            "executable_sha256": executable_sha256,
            "chain": chain,
        }
    # Host explicitly supplies the installed identity; never learn one from the project.
    for name, expected in ((str(executable), executable_sha256), *chain):
        with git_s1._pin_installation(Path(name), expected, lambda: None):
            pass
    runtime._verification_export_boundary(state, workspace, workspace)
    await runtime._sessions.require_owner(task.metadata.get("session_id"), principal)
    runtime._local_git_reviews[task.id] = state


async def capture_source(runtime, frame, sandbox):
    """At real SANDBOX creation, before Agent effects, freeze the Git baseline."""
    from Sprout.sandbox.isolated_git_worktree import IsolatedGitWorktreeSandbox

    task, workspace = await runtime._project_analysis_subject(frame)
    state = runtime._local_git_reviews[task.id]
    if (
        frame.node.type.value != "sandbox"
        or state.get("source")
        or replace(task, status=state["task"].status) != state["task"]
        or workspace != state["workspace"]
    ):
        raise PermissionError("local_git_source_registration_denied")
    # Reuse the existing B-02 management pair, then re-read through G1's native opens.
    _, metadata = IsolatedGitWorktreeSandbox(workspace)._check_git_root(sandbox.root)
    common = workspace.root / ".git"
    runtime._verification_export_boundary(state, workspace, sandbox)

    def authorize():
        runtime._git_broker._check_review_policy(task, state["paths"], sandbox.root)

    with (
        pin_execution_directory(metadata) as meta_pin,
        pin_execution_directory(common) as common_pin,
    ):
        head = git_s1._snapshot(metadata, "HEAD", authorize, 4096)
        if head is None:
            raise PermissionError("local_git_head_missing")
        baseline = head.data.decode("ascii").strip()
        if baseline.startswith("ref: "):
            ref = baseline[5:]
            if not re.fullmatch(r"refs/heads/[A-Za-z0-9_/-]+", ref) or ".." in ref:
                raise PermissionError("local_git_ref_invalid")
            target = git_s1._snapshot(common, ref, authorize, 128)
            if target is None:
                raise PermissionError("local_git_loose_baseline_required")
            baseline = target.data.decode("ascii").strip()
        if not re.fullmatch("[0-9a-f]{40}", baseline):
            raise PermissionError("local_git_baseline_invalid")
        registered = runtime._frozen_sandboxes[task.id]
        view = git_s1._ReviewView(
            state["executable"],
            state["executable_sha256"],
            state["chain"],
            baseline,
            metadata,
            common,
            state["paths"],
            sandbox,
            registered[3],
            state["parent"],
            state["parent_identity"],
            digest(
                {
                    "subject": binding_subject(task, workspace),
                    "scope": scope_digest(task.delegation_scope),
                    "session": task.metadata.get("session_id"),
                }
            ),
            meta_pin.identity,
            common_pin.identity,
        )
        git_s1._source(view, authorize)  # Exact .git/backlink/commondir/HEAD/index contract.
        state["source"] = view  # Not executable until a completed Receipt binds it below.


async def bind(
    runtime,
    task_id,
    principal,
    *,
    receipt,
    executable,
    executable_sha256,
    baseline,
    metadata_root,
    common_root,
    paths,
    chain,
):
    from Sprout.execution.apply_plan import receipt_context
    from Sprout.tools.context_keys import _current_project_node

    frame = _current_project_node()
    task, workspace, payload, candidate, _ = await receipt_context(runtime, receipt, principal)
    if (
        frame is None
        or frame.runtime is not runtime
        or frame.node.type.value != "approval"
        or frame.task.id != task_id
        or task.id != task_id
        or receipt.node_id not in frame.node.dependencies
    ):
        raise PermissionError("local_git_binding_owner_required")
    await runtime._project_analysis_subject(frame)
    state = runtime._local_git_reviews.get(task_id)
    view = state.get("source") if state else None
    if (
        view is None
        or state.get("git") is not None
        or view.sandbox is not candidate["sandbox"]
        or state["workspace"] != workspace
        or (
            Path(executable),
            executable_sha256,
            baseline,
            Path(metadata_root),
            Path(common_root),
            paths,
            chain,
        )
        != (
            view.executable,
            view.executable_sha256,
            view.baseline,
            view.metadata,
            view.common,
            view.paths,
            view.chain,
        )
        or set(view.paths) != {x["path"] for x in payload["after"]}
    ):
        raise PermissionError("local_git_binding_source_changed")
    state["receipt"] = receipt
    state["git"] = view
    return view


async def authorize(runtime, state, frame, task, workspace):
    """Fresh Guard/subject checks. No repeated execution or cached file ALLOW."""
    receipt = state.get("receipt")
    entry = runtime._local_acceptance_receipts.get(receipt)
    if entry is None or receipt.issuer is not runtime:
        raise PermissionError("local_git_receipt_revoked")
    payload, candidate = entry
    node = await runtime._metadata_or_raise().get_execution_node(receipt.node_id)
    guard = node.metadata.get("execution_guard", {}) if node else {}
    if (
        task.id != receipt.task_id
        or workspace != candidate["workspace"]
        or replace(task, status=candidate["task"].status) != candidate["task"]
        or payload_digest(payload) != receipt.payload_sha256
        or node is None
        or node.task_id != task.id
        or node.type.value != "evaluation"
        or node.status.value != "completed"
        or guard.get("state") != "completed"
        or guard.get("owner") != receipt.owner
        or payload_digest(node.metadata.get("output")) != payload["output_sha256"]
        or frame.node.type.value == "apply"
        and state.get("evidence") is None
    ):
        raise PermissionError("local_git_receipt_origin_changed")
    if frame.node.type.value == "apply":
        candidate_output = state.get("publication_candidate")
        if candidate_output is None or candidate_output[0] not in frame.node.dependencies:
            raise PermissionError("local_git_publication_source_missing")
        approval = await runtime._metadata_or_raise().get_execution_node(candidate_output[0])
        approval_guard = approval.metadata.get("execution_guard", {}) if approval else {}
        if (
            approval is None
            or approval.task_id != task.id
            or approval.type.value != "approval"
            or approval.status.value != "completed"
            or approval_guard.get("state") != "completed"
            or approval_guard.get("owner") != candidate_output[1]
            or payload_digest(approval.metadata.get("output")) != candidate_output[2]
        ):
            raise PermissionError("local_git_publication_guard_unconfirmed")
    # Current Principal/Session/Scope and original coroutine were freshly checked
    # by _project_analysis_subject. Executor applicability is checked at bind,
    # publication and Apply, not mistaken for per-file Git read authority.


async def review(runtime, frame, receipt):
    state = runtime._local_git_reviews.get(frame.task.id)
    if state is None or state.get("source") is None:
        raise PermissionError("local_git_host_configuration_required")
    view = state["source"]
    if state.get("git") is None:
        await runtime.bind_task_git_view(
            frame.task.id,
            frame.task.actor,
            executable=view.executable,
            executable_sha256=view.executable_sha256,
            baseline=view.baseline,
            metadata_root=view.metadata,
            common_root=view.common,
            paths=view.paths,
            chain=view.chain,
            local_receipt=receipt,
        )
    if state.get("evidence") is None:
        if state.get("attempted"):
            raise ToolExecutionUnconfirmed("local_git_review_unconfirmed; no automatic retry")
        state["attempted"] = True
        task, workspace = await runtime._project_analysis_subject(frame)
        result = await runtime._git_broker.review_diff(workspace, scope=task.delegation_scope)
        if not result.allowed:
            raise PermissionError(result.reason or "local_git_review_unavailable")
        evidence = json.loads(result.output)
        payload = runtime._local_acceptance_receipts[receipt][0]
        if (
            evidence.get("status") != "available"
            or evidence.get("paths") != list(view.paths)
            or any(
                evidence["current_files"].get(x["path"]) is None
                or evidence["current_files"][x["path"]]["sha256"] != x["sha256"]
                or evidence["current_files"][x["path"]]["identity"] != x["identity"]
                for x in payload["after"]
            )
        ):
            raise PermissionError("local_git_receipt_snapshot_changed")
        state["evidence"] = evidence
    await validate(runtime, frame, receipt, state["evidence"])
    return deepcopy(state["evidence"])


async def validate(runtime, frame, receipt, evidence):
    state = runtime._local_git_reviews.get(frame.task.id)
    if (
        state is None
        or state.get("receipt") is not receipt
        or evidence != state.get("evidence")
        or state.get("git_active", {}).get("processes")
    ):
        raise PermissionError("local_git_review_not_settled")
    await git_s1.validate_review(state["git"], runtime, frame, runtime._git_broker, evidence)


async def base_versions(runtime, frame):
    """Read only selected baseline blobs through the same G1 metadata reader."""
    state = runtime._local_git_reviews[frame.task.id]
    view = state["git"]
    await runtime._git_review_record(view, runtime._git_broker, frame)

    def check():
        runtime._git_broker._check_review_policy(frame.task, view.paths, view.sandbox.root)

    _, _, reader = git_s1._source(view, check)
    objects = git_s1._LooseObjects(view, reader)
    return {p: objects.file(p) for p in view.paths}
