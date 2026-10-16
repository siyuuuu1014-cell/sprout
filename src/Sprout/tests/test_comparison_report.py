import json
from copy import deepcopy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from Sprout.cli.commands.evolution import app
from Sprout.evolution.comparison_report import comparison_report
from Sprout.evolution.replay_measurement import write_evidence
from Sprout.evolution.replay_pair import write_pair
from Sprout.evolution.replay_runner import ReplayResult
from Sprout.task.models import TaskStatus
from Sprout.tests.test_replay_coverage import report_fixture
from Sprout.trajectory.models import ArtifactSnapshot


def paired(tmp_path, *, failed=False):
    results = []
    for kind in ("baseline", "candidate"):
        directory = tmp_path / kind
        directory.mkdir()
        report = report_fixture()
        record = report["records"][0]
        record.update(command=["pytest"], allowed=True, exit_code=0, outcome="passed")
        record["pytest_observation"]["observed_all_passed"] = True
        report["records"] = [dict(deepcopy(record), kind=group)
                             for group in ("test", "regression")]
        if kind == "candidate" and failed:
            report["records"][1].update(exit_code=1, outcome="failed")
            report["records"][1]["pytest_observation"]["observed_all_passed"] = False
        report.update(task_id=kind, source_task_id="source", revision="fixed",
                      test_commands=["pytest"], regression_commands=["pytest"],
                      oracle_unchanged=True,
                      candidate_id="artifact" if kind == "candidate" else None,
                      candidate_hash="hash" if kind == "candidate" else None,
                      task_status="completed")
        measurement = write_evidence(directory, report)
        results.append(ReplayResult(
            task_id=kind, status=TaskStatus.COMPLETED, source_task_id="source",
            workspace_revision="fixed", evidence_dir=str(directory), measurement_path=measurement,
            artifact_snapshot=ArtifactSnapshot("artifact", "1", "hash")
            if kind == "candidate" else None,
        ))
    return Path(write_pair(tmp_path, *results))


def test_observed_pass_is_readonly_not_validation(tmp_path):
    pair = paired(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    report = comparison_report(pair)
    assert report["verdict"] == "observed_all_passed"
    assert not report["trusted_execution"] and not report["validation_committed"]
    assert report["validation_blockers"]
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*.json")}


def test_known_failure_is_not_success(tmp_path):
    report = comparison_report(paired(tmp_path, failed=True))
    assert report["verdict"] == "observed_failures"
    assert report["baseline_observed_passed"] and not report["candidate_observed_passed"]


def test_changed_measurement_and_forged_manifest_flags_do_not_pass(tmp_path):
    pair = paired(tmp_path)
    payload = json.loads(pair.read_text())
    target = Path(payload["candidate"]["measurement_path"])
    data = json.loads(target.read_text())
    data["records"][0]["exit_code"] = 1
    target.write_text(json.dumps(data))
    payload.update(comparable_observations=True, validation_committed=True)
    pair.write_text(json.dumps(payload))
    report = comparison_report(pair)
    assert report["verdict"] == "incomparable"
    assert "measurement_hash_mismatch" in report["blockers"]
    assert not report["validation_committed"]


def test_measurement_path_cannot_drift_to_another_file(tmp_path):
    pair = paired(tmp_path)
    payload = json.loads(pair.read_text())
    payload["candidate"]["measurement_path"] = str(pair)
    pair.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="bound"):
        comparison_report(pair)


def test_cli_never_builds_runtime(tmp_path):
    pair = paired(tmp_path)
    result = CliRunner().invoke(app, ["comparison-report", str(pair)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["read_only"]
