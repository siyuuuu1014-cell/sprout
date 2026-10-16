import json

import pytest

from Sprout.evolution.replay_oracle import integrity, snapshot
from Sprout.evolution.replay_pair import write_pair
from Sprout.evolution.replay_runner import ReplayResult
from Sprout.task.models import TaskStatus
from Sprout.trajectory.models import ArtifactSnapshot


def test_oracle_detects_changes_additions_and_removals(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    test = tests / "test_app.py"
    test.write_text("assert True", encoding="utf-8")
    frozen = snapshot(tmp_path, ("tests",))
    assert integrity(tmp_path, ("tests",), frozen)
    test.write_text("assert False", encoding="utf-8")
    assert not integrity(tmp_path, ("tests",), frozen)
    test.write_text("assert True", encoding="utf-8")
    extra = tests / "conftest.py"
    extra.write_text("# changed collection", encoding="utf-8")
    assert not integrity(tmp_path, ("tests",), frozen)
    extra.unlink()
    test.unlink()
    assert not integrity(tmp_path, ("tests",), frozen)


@pytest.mark.parametrize("paths", [(), ("../outside",), ("missing",)])
def test_oracle_rejects_missing_or_unconfined_paths(tmp_path, paths):
    with pytest.raises(ValueError):
        snapshot(tmp_path, paths)


def test_waiting_pair_has_no_validation(tmp_path):
    result = ReplayResult("", TaskStatus.WAITING_RESOURCE)
    report = json.loads(open(write_pair(tmp_path, result, result), encoding="utf-8").read())
    assert not report["comparable_observations"]
    assert not report["validation_committed"]


def test_pair_compares_frozen_content_and_binds_measurement_hashes(tmp_path):
    from dataclasses import replace

    payload = {
        "task_id": "baseline",
        "source_task_id": "source",
        "revision": "commit",
        "test_commands": ["pytest"],
        "regression_commands": ["pytest regression"],
        "oracle_snapshot": {"tests/test_app.py": "digest"},
        "oracle_unchanged": True,
        "records": [
            {"kind": "test", "command": ["pytest"], "allowed": True, "exit_code": 0},
            {
                "kind": "regression",
                "command": ["pytest", "regression"],
                "allowed": True,
                "exit_code": 0,
            },
        ],
    }
    left = tmp_path / "left.json"
    left.write_text(json.dumps(payload), encoding="utf-8")
    right = tmp_path / "right.json"
    candidate_payload = {
        **payload,
        "task_id": "candidate",
        "candidate_id": "skill",
        "candidate_hash": "hash",
    }
    right.write_text(json.dumps(candidate_payload), encoding="utf-8")
    baseline = ReplayResult(
        "baseline",
        TaskStatus.COMPLETED,
        measurement_path=str(left),
        source_task_id="source",
        workspace_revision="commit",
    )
    candidate = replace(
        baseline,
        task_id="candidate",
        measurement_path=str(right),
        artifact_snapshot=ArtifactSnapshot("skill", "1", "hash"),
    )
    report = json.loads(open(write_pair(tmp_path, baseline, candidate), encoding="utf-8").read())
    assert report["comparable_observations"]
    assert len(report["measurement_hashes"]) == 2
    assert not report["validation_committed"]
    right.write_text(json.dumps({**candidate_payload, "oracle_unchanged": False}), encoding="utf-8")
    report = json.loads(open(write_pair(tmp_path, baseline, candidate), encoding="utf-8").read())
    assert not report["comparable_observations"]


@pytest.mark.parametrize("change", ["missing", "wrong-command", "withheld", "unknown"])
def test_incomplete_command_records_are_not_comparable(change):
    from Sprout.evolution.replay_pair import complete_records

    report = {
        "test_commands": ["pytest"],
        "regression_commands": ["pytest regression"],
        "records": [
            {"kind": "test", "command": ["pytest"], "allowed": True, "exit_code": 0},
            {
                "kind": "regression",
                "command": ["pytest", "regression"],
                "allowed": True,
                "exit_code": 0,
            },
        ],
    }
    assert complete_records(report)
    if change == "missing":
        report["records"].pop()
    elif change == "wrong-command":
        report["records"][1]["command"] = ["echo", "success"]
    elif change == "withheld":
        report["records"][1]["allowed"] = False
    else:
        report["records"][1]["exit_code"] = None
    assert not complete_records(report)
