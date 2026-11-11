"""Content-addressed review evidence for foreground filesystem experiments."""

import hashlib
import json
from dataclasses import asdict
from difflib import unified_diff

from Sprout.execution.host_acceptance import pin_execution_directory
from Sprout.sandbox.filesystem import capture_snapshot, checked_label


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _version(snapshot):
    if snapshot.reason == "snapshot_not_found":
        return None
    if not snapshot.ok:
        raise PermissionError("filesystem_review_snapshot_unavailable")
    content = snapshot.data.decode("utf-8")
    if "\x00" in content:
        raise ValueError("filesystem_review_binary_unsupported")
    return {"content": content, "sha256": snapshot.digest, "identity": asdict(snapshot.identity)}


def capture_changes(sandbox, before, task_id, workspace_id, authorize, *, overrides=None,
                    command=None):
    sandbox.check()
    changes, conflicts = [], []
    with pin_execution_directory(sandbox.original) as original:
        if original.identity != sandbox.original_identity:
            raise PermissionError("filesystem_review_workspace_changed")
        for label, baseline in sorted({**sandbox.sources, **before}.items()):
            checked_label(label, sandbox.original, overrides)
            checked_label(label, sandbox.ref.root, overrides)
            target = sandbox.original / label

            def check(target=target):
                original.check()
                authorize(target)

            previous = _version(baseline)
            if previous is None:
                current = capture_snapshot(sandbox.original, target, check, limit=0)
                changed = current.reason != "snapshot_not_found"
            else:
                current = capture_snapshot(sandbox.original, target, check)
                _version(current)
                changed = (current.digest, current.identity) != (baseline.digest, baseline.identity)
            if changed:
                conflicts.append(label)
            if label not in before:
                continue
            final = _version(capture_snapshot(
                sandbox.ref.root, sandbox.ref.root / label, sandbox.check))
            if final is None:
                if previous is not None:
                    raise ValueError("filesystem_review_deletion_unsupported")
                continue
            if previous is not None and previous["sha256"] == final["sha256"]:
                continue
            changes.append({"path": label, "kind": "added" if previous is None else "modified",
                            "before": previous, "after": final})
    payload = {"schema": "filesystem_changes_v1", "task_id": task_id,
               "workspace_id": workspace_id, "sandbox_id": sandbox.ref.id,
               "sandbox_identity": sandbox.identity,
               "workspace_identity": sandbox.original_identity,
               "sources": {path: {"sha256": snap.digest, "identity": asdict(snap.identity)}
                           for path, snap in sorted(sandbox.sources.items())},
               "changes": changes, "conflicts": conflicts,
               "verification": {"independent": "not_run", "coding_command": command},
               "apply_available": False}
    return {"payload": payload, "fingerprint": _digest(payload)}


def checked_changes(review, task_id, workspace_id):
    payload = review["payload"]
    if (payload["schema"] != "filesystem_changes_v1"
            or (payload["task_id"], payload["workspace_id"]) != (task_id, workspace_id)
            or _digest(payload) != review["fingerprint"]):
        raise ValueError("filesystem_review_evidence_mismatch")
    for change in payload["changes"]:
        for version in (change["before"], change["after"]):
            if version is not None and (
                    hashlib.sha256(version["content"].encode()).hexdigest() != version["sha256"]):
                raise ValueError("filesystem_review_content_mismatch")
    return payload


def render_review(review, task_id, workspace_id, *, verification_status="not_run"):
    payload = checked_changes(review, task_id, workspace_id)
    lines = [f'ChangeSet {review["fingerprint"]}',
             f"Independent verification: {verification_status.upper()}"]
    command = payload["verification"]["coding_command"]
    if command:
        lines.append(f'Coding command: {command["status"]}, exit code {command["exit_code"]}')
    if payload["conflicts"]:
        lines.append("Source conflicts: " + ", ".join(payload["conflicts"]))
    if not payload["changes"]:
        lines.append("No changes")
    for change in payload["changes"]:
        path, before, after = change["path"], change["before"], change["after"]
        lines.append(f'{change["kind"]}: {path}')
        lines.append("")
        lines.extend("    " + line for line in unified_diff(
            [] if before is None else before["content"].splitlines(keepends=True),
            after["content"].splitlines(keepends=True),
            fromfile="/dev/null" if before is None else path, tofile=path, lineterm=""))
        lines.append("")
    text = "\n".join(line.removesuffix("\n") for line in lines)
    return "".join(char if char in "\n\t" or char.isprintable() else ascii(char)[1:-1]
                   for char in text)


def apply_gate(review, task_id, workspace_id, *, verification_status="not_run"):
    """Evaluate the current non-Git boundary without issuing a write permit."""
    try:
        payload = checked_changes(review, task_id, workspace_id)
    except (KeyError, TypeError, ValueError):
        return {"waiting": True, "waiting_kind": "resource",
                "error": "filesystem_review_evidence_mismatch", "filesystem_prewrite": True}
    if payload["conflicts"]:
        reason = "filesystem_source_conflict_at_review"
    elif not payload["changes"]:
        return {"applied": False, "status": "no_changes"}
    else:
        # A coding-command result and caller-supplied verification fields cannot
        # provide the independently issued acceptance receipt required by Apply.
        reason = {
            "passed": "filesystem_apply_coordinator_unavailable",
            "failed": "filesystem_verification_failed",
        }.get(verification_status, "filesystem_verification_required")
    return {"waiting": True, "waiting_kind": "resource", "error": reason,
            "filesystem_prewrite": True}


def verification_manifest(payload):
    """Require the whole candidate, including unchanged captured inputs."""
    expected = {path: source["sha256"] for path, source in payload["sources"].items()}
    expected.update((change["path"], change["after"]["sha256"])
                    for change in payload["changes"])
    return expected


def verification_status(node, review, task_id, workspace_id):
    from Sprout.orchestration.models import NodeStatus, NodeType

    if (node is None or node.task_id != task_id or node.type is not NodeType.EVALUATION
            or node.status is not NodeStatus.COMPLETED):
        return "not_run"
    guard = node.metadata.get("execution_guard", {})
    proof = node.metadata.get("output") or {}
    if guard.get("state") != "completed" or (
            proof.get("task_id"), proof.get("workspace_id"), proof.get("node_id"),
            proof.get("owner"), proof.get("changeset_sha256"),
    ) != (task_id, workspace_id, node.id, guard.get("owner"), review["fingerprint"]):
        return "not_run"
    payload = checked_changes(review, task_id, workspace_id)
    if proof.get("input_manifest") != verification_manifest(payload):
        return "not_run"
    tests = proof.get("test_results")
    if proof.get("verified") is True and proof.get("status") == "passed" and tests and all(
            result.get("passed") is True and result.get("executed") is True for result in tests):
        return "passed"
    return "failed" if proof.get("status") == "failed" else "not_run"
