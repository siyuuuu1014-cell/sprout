"""Persist paired observations without promoting a growth candidate."""

import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from Sprout.evolution.replay_coverage import compare_structured
from Sprout.evolution.replay_measurement import command_parts
from Sprout.evolution.replay_oracle import digest


def complete_records(report: dict) -> bool:
    """Check coverage of configured commands, never infer a trusted test verdict."""
    planned = [
        (kind, command_parts(command))
        for kind, key in (("test", "test_commands"), ("regression", "regression_commands"))
        for command in report.get(key, ())
    ]
    records = report.get("records", ())
    if not report.get("test_commands") or not report.get("regression_commands"):
        return False
    if len(records) != len(planned):
        return False
    return all(
        isinstance(record, dict)
        and record.get("kind") == kind
        and tuple(record.get("command", ())) == command
        and record.get("allowed") is True
        and type(record.get("exit_code")) is int
        and (not record.get("verification_image") or record.get("input_unchanged") is True)
        for record, (kind, command) in zip(records, planned, strict=True)
    )


def comparable_observations(left, right, baseline, candidate) -> bool:
    return bool(
        left
        and right
        and left["task_id"] != right["task_id"]
        and left["source_task_id"] == right["source_task_id"]
        and left["revision"] == right["revision"]
        and left["test_commands"] == right["test_commands"]
        and left["regression_commands"] == right["regression_commands"]
        and left["oracle_snapshot"]
        and left["oracle_snapshot"] == right["oracle_snapshot"]
        and left["oracle_unchanged"]
        and right["oracle_unchanged"]
        and left.get("candidate_id") is None
        and candidate.artifact_snapshot is not None
        and right.get("candidate_id")
        == (candidate.artifact_snapshot.artifact_id if candidate.artifact_snapshot else None)
        and right.get("candidate_hash") == candidate.artifact_snapshot.content_hash
        and left["source_task_id"] == baseline.source_task_id == candidate.source_task_id
        and left["revision"] == baseline.workspace_revision == candidate.workspace_revision
        and left["task_id"] == baseline.task_id
        and right["task_id"] == candidate.task_id
        and complete_records(left)
        and complete_records(right)
    )


def write_pair(output: Path, baseline, candidate) -> str:
    reports = []
    for result in (baseline, candidate):
        report = (
            json.loads(Path(result.measurement_path).read_text(encoding="utf-8"))
            if result.measurement_path
            else None
        )
        reports.append(report)
    left, right = reports
    comparable = comparable_observations(left, right, baseline, candidate)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"growth-comparison-{uuid4().hex}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "schema_version": 1,
                "baseline": asdict(baseline),
                "candidate": asdict(candidate),
                "measurement_hashes": [digest(report) if report else None for report in reports],
                "comparable_observations": comparable,
                "structured_comparison": compare_structured(left, right),
                "validation_committed": False,
                "blocker": "Trusted test collection and runner identity are not established",
            },
            stream,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    return str(path)
