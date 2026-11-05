"""Shared verification policy for proposals and task completion.

Approval authorizes a diff; only executed, passing checks prove verification.
Keep this pure so local and durable orchestration use the same gate.
"""

import re
from collections.abc import Mapping, Sequence

from Sprout.execution.models import (
    ChangeProposal,
    ProcessResult,
    TestResult,
    VerificationCheck,
    VerificationReport,
)
from Sprout.strategy.acceptance import AcceptanceSpec


def _evidence_blocker(
    results: Sequence[TestResult], verification: object = None,
) -> str:
    failures = [item.name for item in results if item.failed]
    if failures:
        return "Verification failed for " + ", ".join(failures)
    withheld = [item.name for item in results if not item.executed]
    if withheld:
        return "Verification incomplete; commands did not execute: " + ", ".join(withheld)
    if not results:
        return "Verification incomplete; no executed checks are recorded"
    if isinstance(verification, Mapping) and verification.get("status") != "passed":
        return f"Verification incomplete; status={verification.get('status', 'unknown')}"
    if isinstance(verification, Mapping) and (
        verification.get("acceptance_required") or verification.get("acceptance_spec") is not None
    ):
        try:
            spec = AcceptanceSpec.from_payload(verification.get("acceptance_spec"))
        except (ValueError, TypeError):
            return "Verification incomplete; a valid frozen acceptance specification is missing"
        for case in spec.cases:
            if not any(item.executed and item.passed
                       and item.name.startswith(f"acceptance:{case.id}: ") for item in results):
                return f"Verification incomplete; acceptance case {case.id} has no passing result"
        if not verification.get("demo_path") or not any(
            item.executed and item.passed and item.name.startswith("poc: ") for item in results
        ):
            return "Verification incomplete; the minimal demo has no passing result"
    return ""


def classify_verification_command(result: ProcessResult) -> str:
    """Classify observable command outcomes without interpreting arbitrary logs.

    Pytest summaries may veto a pass, but are not proof against a malicious
    runner. Independent evaluation still requires an isolated trusted runner.
    """
    if not result.allowed:
        return "approval_required" if result.needs_approval else "policy_denied"
    if result.exit_code is None:
        return "timeout" if result.error == "Process timed out" else "runner_error"
    parts = result.command
    pytest = bool(parts) and (
        parts[0].replace("\\", "/").rsplit("/", 1)[-1] in {"pytest", "pytest.exe"}
        or parts[1:3] == ("-m", "pytest")
    )
    if pytest and result.exit_code in {2, 3, 4}:
        return "runner_error"
    if pytest and result.exit_code == 5:
        return "missing_results"
    if result.exit_code != 0:
        return "check_failed"
    if pytest and re.search(r"\b[1-9]\d* skipped\b", result.stdout + "\n" + result.stderr):
        return "skipped"
    return "passed"


def build_verification_report(
    results: Sequence[TestResult], verification: object = None, *, overridden: bool = False,
) -> VerificationReport:
    """Normalize old/new evidence while preserving the legacy completion rules."""
    metadata = verification if isinstance(verification, Mapping) else {}
    raw = metadata.get("results", ())
    records = raw if isinstance(raw, (list, tuple)) else ()
    records_by_name = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        command = record.get("command", ())
        if not isinstance(command, (list, tuple)):
            command = ()
        name = record.get("name") or f"{record.get('kind', '')}: {' '.join(map(str, command))}"
        records_by_name[str(name)] = record
    checks = []
    codes = []
    known = {
        "passed", "check_failed", "not_executed", "approval_required", "policy_denied",
        "runner_error", "timeout", "skipped", "missing_results", "baseline_invalid", "UNKNOWN",
        "unverified",
    }
    for result in results:
        record = records_by_name.get(result.name, {})
        outcome = record.get("outcome") or (
            "not_executed" if not result.executed else "passed" if result.passed else "check_failed"
        )
        if not isinstance(outcome, str) or outcome not in known:
            outcome = "missing_results"
        if outcome == "passed" and (not result.executed or not result.passed):
            outcome = "check_failed" if result.executed else "not_executed"
        if outcome != "passed":
            codes.append(outcome)
        command = record.get("command", ())
        checks.append(VerificationCheck(
            name=result.name, outcome=outcome, executed=result.executed,
            passed=result.executed and result.passed and outcome == "passed",
            command=tuple(map(str, command)) if isinstance(command, (tuple, list)) else (),
            cwd=str(record.get("cwd") or metadata.get("cwd") or ""),
            exit_code=record.get("exit_code") if type(record.get("exit_code")) is int else None,
            duration_ms=result.duration_ms, output=result.output,
            approval_id=(record.get("approval_id")
                         if isinstance(record.get("approval_id"), str) else None),
        ))
    baseline = metadata.get("baseline")
    if isinstance(baseline, Mapping) and baseline.get("status") == "invalid":
        codes.append("baseline_invalid")
    blocker = _evidence_blocker(results, verification)
    if blocker and not codes:
        codes.append("missing_evidence")
    if codes and not blocker:
        blocker = "Verification incomplete; " + ", ".join(dict.fromkeys(codes))
    if overridden:
        codes.append("verification_overridden")
        blocker = blocker or "Verification overridden; forced apply is not verified completion"
    return VerificationReport(
        status="failed" if "check_failed" in codes else "incomplete" if blocker else "passed",
        verified=not blocker, blocker=blocker, checks=tuple(checks), overridden=overridden,
        failure_codes=tuple(dict.fromkeys(codes)),
    )


def verification_blocker(
    results: Sequence[TestResult], verification: object = None,
) -> str:
    return build_verification_report(results, verification).blocker


def proposal_verification_report(proposal: ChangeProposal) -> VerificationReport:
    verification = proposal.metadata.get("verification")
    report = build_verification_report(
        proposal.test_results,
        {**verification, "acceptance_required": True}
        if isinstance(verification, Mapping) else verification,
        overridden=bool(proposal.metadata.get("verification_override")),
    )
    # Legacy approvals authorize a diff, not evidence that never existed.
    # Re-verification or the separately audited force path is required.
    if not isinstance(verification, Mapping):
        if not _evidence_blocker(proposal.test_results, verification):
            from dataclasses import replace

            report = replace(report, status="incomplete", verified=False,
                blocker="Verification incomplete; legacy proposal needs requirement tests "
                        "and a minimal demo",
                failure_codes=tuple(dict.fromkeys((*report.failure_codes, "missing_evidence"))))
    return report


def proposal_verification_blocker(proposal: ChangeProposal) -> str:
    return proposal_verification_report(proposal).blocker
