"""Recompute a paired observation verdict; never trust imported pass booleans."""

import json
from pathlib import Path
from types import SimpleNamespace

from Sprout.evolution.comparison_conditions import condition_blockers
from Sprout.evolution.function_provenance import function_provenance
from Sprout.evolution.measurement_provenance import provenance_blockers
from Sprout.evolution.replay_coverage import compare_structured
from Sprout.evolution.replay_oracle import digest
from Sprout.evolution.replay_pair import comparable_observations
from Sprout.evolution.validation_readiness import measurement_readiness


def _read(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Existing regular evidence file up to 8 MiB required")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Evidence must be a JSON object")
    return value


def comparison_report(pair_path: Path) -> dict:
    """Read exact original measurement paths; no refreshing stale pairs or state writes."""
    try:
        pair = _read(pair_path)
        if pair.get("schema_version") != 1:
            raise ValueError("Unsupported comparison schema")
        results, reports = [], []
        for key in ("baseline", "candidate"):
            result = dict(pair[key])
            snapshot = result.get("artifact_snapshot")
            result["artifact_snapshot"] = SimpleNamespace(**snapshot) if snapshot else None
            results.append(SimpleNamespace(**result))
            if not result.get("measurement_path"):
                reports.append(None)
                continue
            path = Path(result["measurement_path"])
            directory = Path(result["evidence_dir"]).resolve(strict=True)
            if path.resolve(strict=True) not in {
                directory / "independent-measurement.json",
                directory / "independent-measurement-completed.json",
            }:
                raise ValueError("Measurement path is not bound to its experiment directory")
            reports.append(_read(path))
        blockers = []
        hashes = [digest(report) if report else None for report in reports]
        if pair.get("measurement_hashes") != hashes:
            blockers.append("measurement_hash_mismatch")
        comparable = comparable_observations(*reports, *results)
        if not comparable:
            blockers.append("paired_run_binding_or_coverage_incomplete")
        if any(not report or report.get("task_status") != "completed"
               or result.status != "completed"
               for report, result in zip(reports, results, strict=True)):
            blockers.append("completed_task_evidence_missing")
        structured = compare_structured(*reports)
        blockers.extend(structured["blockers"])
        conditions = condition_blockers(results, reports)
        provenance = provenance_blockers(results, reports)
        functions = function_provenance(results, reports)
        regressions = function_provenance(results, reports, purpose="regression")
        if (not reports[0] or not reports[1] or not reports[0].get("regression_fingerprint")
                or reports[0].get("regression_fingerprint")
                != reports[1].get("regression_fingerprint")):
            regressions = {"verified": False, "observed_passed": [],
                           "blockers": ["frozen_regression_pair_missing_or_mismatched"]}
        outcomes = []
        for report in reports:
            records = report.get("records", ()) if report else ()
            passed = bool(records) and all(
                record.get("allowed") is True and type(record.get("exit_code")) is int
                and record["exit_code"] == 0 and record.get("outcome") == "passed"
                and record.get("pytest_observation", {}).get("observed_all_passed") is True
                for record in records
            )
            outcomes.append(passed)
        verdict = ("incomparable" if blockers else "observed_all_passed"
                   if all(outcomes) else "observed_failures")
        readiness = measurement_readiness(
            binding_blockers=blockers, condition_blockers=conditions,
            execution_blockers=provenance, pytest_outcomes=outcomes,
            functions=functions, regressions=regressions)
        return {
            "schema_version": 1, "verdict": verdict,
            "comparable_observations": not blockers,
            "baseline_observed_passed": outcomes[0], "candidate_observed_passed": outcomes[1],
            "acceptance_coverage": structured.get("acceptance_coverage", []),
            "blockers": sorted(set(blockers)),
            "validation_blockers": readiness["validation_blockers"],
            "validation_readiness": readiness,
            "function_execution_provenance": functions,
            "regression_execution_provenance": regressions,
            "execution_provenance_verified": not provenance,
            "execution_provenance_blockers": provenance,
            "experiment_conditions_verified": not conditions and not blockers,
            "experiment_condition_blockers": conditions,
            "trusted_execution": False, "validation_committed": False,
            "read_only": True,
        }
    except (KeyError, TypeError, AttributeError, OSError, json.JSONDecodeError) as exc:
        raise ValueError("Comparison evidence is missing or malformed") from exc
