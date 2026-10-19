"""Test-only, allowlisted projections. Never export raw runtime records or logs.

Signatures are observations, not root-cause claims. Unknown text is discarded.
The existing verification report remains the authority; this module never runs,
approves, retries, verifies or applies a task.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from scripts.ci_support.acceptance_probe import acceptance_launch
from Sprout.execution.models import ChangeProposalStatus
from Sprout.orchestration.models import NodeStatus, NodeType
from Sprout.security.approval import ApprovalStatus
from Sprout.task.models import TaskStatus

LABELS = {
    "eval_C01",
    "eval_C02",
    "eval_C03",
    "eval_R01",
    "e2e_delete_approve",
    "e2e_rename_approve",
    "e2e_requirement_true",
    "e2e_proposal",
}
CODES = {
    "passed",
    "check_failed",
    "not_executed",
    "approval_required",
    "policy_denied",
    "runner_error",
    "timeout",
    "skipped",
    "missing_results",
    "baseline_invalid",
    "missing_evidence",
    "verification_overridden",
}
SIGNATURES = {
    "ruff_i001": r"\bI001\b",
    "module_missing": r"ModuleNotFoundError|No module named",
    "import_error": r"ImportError",
    "windows_file_missing": r"\[WinError 2\]",
    "windows_invalid_executable": r"\[WinError 193\]",
    "permission_denied": r"PermissionError|Permission denied|Access is denied",
    "encoding_error": r"UnicodeEncodeError|UnicodeDecodeError",
    "process_timeout": r"Process timed out|TimeoutExpired",
    "command_missing": r"FileNotFoundError|not recognized as|command not found",
    "pytest_collection_error": r"ERROR collecting|Interrupted:.*error.*collection",
    "git_identity_missing": r"Author identity unknown|unable to auto-detect email",
    "git_unsafe_repository": r"detected dubious ownership",
    "git_patch_rejected": r"patch does not apply|patch failed:|corrupt patch",
    "git_line_endings_warning": r"LF will be replaced by CRLF|CRLF will be replaced by LF",
    "apply_not_landed": r"Apply did not land change proposal",
    "verification_blocked": r"Verification incomplete|Verification failed for",
    "git_bad_revision": r"bad revision|ambiguous argument.*HEAD|unknown revision",
}


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _enum(value, allowed):
    # Return the known constant, never a caller's arbitrary string/subclass.
    return next((str(item) for item in allowed if value == item), "unknown")


def _bool(value):
    return value if type(value) is bool else None


def _int(value):
    return value if type(value) is int and -(2**32) <= value <= 2**32 else None


def _signals(*values):
    text = "\n".join(value for value in values if isinstance(value, str))
    return [code for code, pattern in SIGNATURES.items() if re.search(pattern, text)]


def _git(repo, *args):
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=10, check=True
    )
    return result.stdout.strip()


def git_state(repo):
    """Only numeric counts and validated revisions; no diff/config/status bodies."""
    try:
        head = _git(repo, "rev-parse", "HEAD")
        return {
            "head": head if re.fullmatch(r"[0-9a-f]{40,64}", head) else None,
            "commits": int(_git(repo, "rev-list", "--count", "HEAD")),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"head": None, "commits": None}


def environment():
    def version(text):
        match = re.fullmatch(r"(?:git version )?(\d+(?:\.\d+){1,3})(?:\.windows\.\d+)?", text)
        return match[1] if match else "unknown"

    try:
        git_version = version(_git(None, "--version"))
    except (OSError, subprocess.SubprocessError):
        git_version = "unknown"
    try:
        ruff_version = version(importlib.metadata.version("ruff"))
    except importlib.metadata.PackageNotFoundError:
        ruff_version = "unknown"
    return {
        "platform": _enum(sys.platform, {"win32", "linux", "darwin"}),
        "python": ".".join(map(str, sys.version_info[:3])),
        "git": git_version,
        "ruff": ruff_version,
        "stdout_encoding": _enum(
            (sys.stdout.encoding or "").lower(), {"utf-8", "cp1252", "gbk", "ascii"}
        ),
    }


def project(
    label,
    *,
    task=None,
    nodes=(),
    verification=None,
    approvals=(),
    proposals=(),
    before=None,
    after=None,
    missing=(),
):
    """Explicit field whitelist. No metadata, names, commands, paths or free text escape."""
    if label not in LABELS:
        raise ValueError("Unknown diagnostic fixture")
    report = verification or {}
    checks = []
    for index, item in enumerate(_get(report, "checks", ())):
        name = _get(item, "name", "")
        kind = "unknown"
        if isinstance(name, str):
            kind = next(
                (
                    prefix
                    for prefix in ("pytest", "ruff", "acceptance", "poc")
                    if name.startswith(prefix + ":")
                ),
                "unknown",
            )
        command = _get(item, "command", ())
        if kind == "unknown" and isinstance(command, (tuple, list)) and command:
            executable = str(command[0]).replace("\\", "/").rsplit("/", 1)[-1]
            kind = {
                "pytest": "pytest",
                "pytest.exe": "pytest",
                "ruff": "ruff",
                "ruff.exe": "ruff",
            }.get(executable, "unknown")
        checks.append(
            {
                "index": index,
                "name": kind,
                "status": (
                    "not_run"
                    if _get(item, "executed") is False
                    else "passed"
                    if _get(item, "executed") is True and _get(item, "passed") is True
                    else "failed"
                    if _get(item, "executed") is True and _get(item, "passed") is False
                    else "unknown"
                ),
                "outcome": _enum(_get(item, "outcome"), CODES),
                "exit_code": _int(_get(item, "exit_code")),
                "signals": _signals(_get(item, "output")),
            }
        )
        if kind == "acceptance":
            checks[-1]["launch_diagnostic"] = acceptance_launch(
                command, _get(item, "output"),
                executed=_get(item, "executed"), passed=_get(item, "passed"),
            )
    projected_nodes = []
    for index, item in enumerate(nodes):
        metadata = _get(item, "metadata", {})
        output = _get(metadata, "output", {})
        projected_nodes.append(
            {
                "index": index,
                "type": _enum(_get(item, "type"), NodeType),
                "status": _enum(_get(item, "status"), NodeStatus),
                "attempts": _int(_get(item, "attempts")),
                "result_status": _enum(_get(output, "status"), CODES | {"failed", "not_verified"}),
                "applied": _bool(_get(output, "applied")),
                "execution_confirmed": _bool(_get(output, "execution_confirmed")),
                "signals": _signals(
                    _get(metadata, "error"),
                    _get(metadata, "last_error"),
                    _get(output, "reason"),
                    _get(output, "error"),
                ),
            }
        )
    before, after = before or {}, after or {}
    old_count, new_count = _int(before.get("commits")), _int(after.get("commits"))
    head_changed = (
        before["head"] != after["head"] if before.get("head") and after.get("head") else None
    )
    apply_nodes = [item for item in projected_nodes if item["type"] == "apply"]
    applied = next(
        (item["applied"] for item in reversed(apply_nodes) if item["applied"] is not None), None
    )
    proposal_states = [_enum(_get(item, "status"), ChangeProposalStatus) for item in proposals]
    if "applied" in proposal_states:
        applied = True
    failed_nodes = [
        item["index"]
        for item in projected_nodes
        if item["status"] == "failed" or item["result_status"] in {"failed", "check_failed"}
    ]
    failed_checks = [item["index"] for item in checks if item["status"] == "failed"]
    return {
        "schema_version": 1,
        "fixture": label,
        "environment": environment(),
        "observation_only": True,
        "root_cause": "not_established",
        "task_status": _enum(_get(task, "status"), TaskStatus),
        "failure_codes": [_enum(code, CODES) for code in _get(report, "failure_codes", ())],
        "nodes": projected_nodes,
        "verification": {
            "status": _enum(_get(report, "status"), {"passed", "failed", "incomplete"}),
            "verified": _bool(_get(report, "verified")),
            "checks": checks,
        },
        "approval_states": [_enum(_get(item, "status"), ApprovalStatus) for item in approvals],
        "proposal_states": proposal_states,
        "apply": {
            "attempt_observed": any((item["attempts"] or 0) > 0 for item in apply_nodes),
            "execution_confirmed": next(
                (
                    item["execution_confirmed"]
                    for item in apply_nodes
                    if item["execution_confirmed"] is not None
                ),
                None,
            ),
            "succeeded": applied,
            "reason_categories": sorted(
                {signal for item in apply_nodes for signal in item["signals"]}
            ),
        },
        "git": {
            "head_changed": head_changed,
            "before_count": old_count,
            "actual_count": new_count,
            "expected_minimum_count": old_count + 1 if old_count is not None else None,
        },
        "first_failure": {
            "node_index": next(iter(failed_nodes), None),
            "check_index": next(iter(failed_checks), None),
            "ordering": "record_order_not_wall_clock",
        },
        "missing": [
            name
            for name in (
                "task",
                "nodes",
                "verification",
                "approvals",
                "proposals",
                "baseline",
                "git",
                "read_error",
            )
            if name in missing
        ],
    }


def emit(summary):
    """Only called with project() results; fixed file names, no source-file copying."""
    destination = os.environ.get("SPROUT_TEST_DIAGNOSTICS")
    if destination:
        root = Path(destination)
        root.mkdir(parents=True, exist_ok=True)
        (root / (summary["fixture"] + ".json")).write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )


async def runtime_failure(runtime, task_id, repo, before, label):
    """Read-only public queries plus test metadata store, before Runtime.stop()."""
    missing = []

    async def query(name, operation):
        try:
            return await operation()
        except Exception:  # noqa: BLE001 - never publish exception text or mask original verdict
            missing.append(name)
            return None

    task = await query("task", lambda: runtime.get_task(task_id))
    nodes = await query("nodes", lambda: runtime.storage.metadata.list_execution_nodes(task_id))
    verification = await query("verification", lambda: runtime.get_verification_report(task_id))
    approvals = await query("approvals", runtime.list_approvals)
    proposals = await query("proposals", lambda: runtime.list_change_proposals(task_id))
    emit(
        project(
            label,
            task=task,
            nodes=nodes or (),
            verification=verification,
            approvals=[item for item in approvals or () if _get(item, "task_id") == task_id],
            proposals=proposals or (),
            before=before,
            after=git_state(repo),
            missing=missing,
        )
    )


def evaluation_failures(report, temporary_root):
    """Reuse only fixed known evaluator JSON names under this test's tmp_path."""
    for result in report["results"]:
        label = "eval_" + result["id"]
        if result["scenario_passed"] or label not in LABELS:
            continue
        directory = Path(result["evidence_dir"])
        if directory.is_symlink() or not directory.resolve().is_relative_to(
            temporary_root.resolve()
        ):
            raise ValueError("Evaluator evidence outside test directory")
        missing = []

        def read(name, default, directory=directory, missing=missing):
            path = directory / (name + ".json")
            try:
                if path.is_symlink():
                    raise ValueError("Symlinked evidence")
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                missing.append(name)
                return default

        baseline = read("baseline", {})
        before = {"head": baseline.get("revision"), "commits": None}
        if re.fullmatch(r"[0-9a-f]{40,64}", str(before["head"])):
            try:
                before["commits"] = int(
                    _git(directory / "repo", "rev-list", "--count", before["head"])
                )
            except (OSError, ValueError, subprocess.SubprocessError):
                missing.append("git")
        emit(
            project(
                label,
                task=read("task", {}),
                nodes=read("nodes", []),
                verification=read("verification", {}),
                approvals=read("approvals", []),
                proposals=read("proposals", []),
                before=before,
                after=git_state(directory / "repo"),
                missing=missing,
            )
        )
