import json
import subprocess
import sys
from pathlib import Path

import pytest

from Sprout.evolution import pytest_probe
from Sprout.evolution.pytest_measurement import pytest_arguments, summarize
from Sprout.evolution.replay_measurement import measure
from Sprout.execution.models import ProcessResult


def fixture_payload():
    return {
        "schema_version": 1,
        "nonce": "nonce",
        "exit_code": 0,
        "collected": ["test_app.py::test_ok"],
        "collection_errors": [],
        "reports": [
            {"nodeid": "test_app.py::test_ok", "phase": phase, "outcome": "passed"}
            for phase in ("setup", "call", "teardown")
        ],
        "runner": {"probe_hash": "hash"},
    }


def test_structured_pass_is_observation_not_trust():
    summary = summarize(fixture_payload(), "nonce", 0, "hash")
    assert summary["observed_all_passed"]
    assert summary["counts"]["passed"] == 1
    assert not summary["trusted_execution"]


@pytest.mark.parametrize(
    "problem", ["empty", "skip", "fail", "incomplete", "nonce", "hash", "collection"]
)
def test_incomplete_or_mismatched_observations_fail_closed(problem):
    payload = fixture_payload()
    if problem == "empty":
        payload["collected"] = []
    elif problem in {"skip", "fail"}:
        payload["reports"][1]["outcome"] = {"skip": "skipped", "fail": "failed"}[problem]
    elif problem == "incomplete":
        payload["reports"].pop()
    elif problem == "nonce":
        payload["nonce"] = "old-run"
    elif problem == "hash":
        payload["runner"]["probe_hash"] = "modified"
    else:
        payload["collection_errors"] = ["test_broken.py"]
    assert not summarize(payload, "nonce", 0, "hash")["observed_all_passed"]


def test_only_supported_pytest_commands_use_observer():
    assert pytest_arguments(("python", "-m", "pytest", "tests")) == ("tests",)
    assert pytest_arguments(("pytest", "tests")) == ("tests",)
    assert pytest_arguments(("echo", "passed")) is None


def test_probe_records_real_pytest_collection_and_phases(tmp_path):
    test = tmp_path / "test_probe_fixture.py"
    test.write_text("def test_ok():\n    assert 2 + 2 == 4\n", encoding="utf-8")
    output = tmp_path / "observation.json"
    probe = Path(pytest_probe.__file__).resolve()
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(probe),
            str(output),
            "real-nonce",
            "-q",
            "-p",
            "no:cacheprovider",
            str(test),
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = summarize(payload, "real-nonce", 0, payload["runner"]["probe_hash"])
    assert summary["observed_all_passed"]
    assert summary["collected_count"] == 1
    assert payload["runner"]["pytest_version"]


async def test_broker_observation_missing_report_never_passes(tmp_path):
    from unittest.mock import AsyncMock

    runtime = AsyncMock()
    runtime.run_task_process.return_value = ProcessResult(("python",), exit_code=0)
    records = await measure(
        runtime, "fresh-task", ("pytest",), ("pytest regression",), evidence_dir=tmp_path
    )
    assert all(item["outcome"] == "incomplete_pytest_observation" for item in records)
    assert all(not item["pytest_observation"]["trusted_execution"] for item in records)
    assert records[0]["command"] == ("pytest",)
    assert records[0]["executed_command"][0] == sys.executable
    assert records[0]["executed_command"][1] == "-I"
