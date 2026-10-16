import json
from unittest.mock import AsyncMock

import pytest

from Sprout.evolution.replay_measurement import command_parts, measure, write_evidence
from Sprout.execution.models import ProcessResult


async def test_measurement_runs_each_group_once_and_preserves_argv():
    runtime = AsyncMock()
    runtime.run_task_process.return_value = ProcessResult(("python",), exit_code=0)
    records = await measure(runtime, "new-task", ('python "test with spaces.py"',), ("pytest",))
    assert [item["kind"] for item in records] == ["test", "regression"]
    assert all(item["outcome"] == "passed" for item in records)
    assert runtime.run_task_process.call_args_list[0].args == (
        "new-task",
        ("python", "test with spaces.py"),
    )
    assert runtime.run_task_process.call_count == 2


@pytest.mark.parametrize(
    "result,expected",
    [
        (ProcessResult(("pytest",), allowed=False, approval_id="pending"), "approval_required"),
        (ProcessResult(("pytest",), error="Process timed out"), "timeout"),
    ],
)
async def test_unknown_or_withheld_stops_without_retry(result, expected):
    runtime = AsyncMock()
    runtime.run_task_process.return_value = result
    records = await measure(runtime, "new-task", ("pytest",), ("pytest regression",))
    assert records[0]["outcome"] == expected
    assert runtime.run_task_process.call_count == 1


async def test_skipped_and_no_tests_are_not_passes():
    runtime = AsyncMock()
    runtime.run_task_process.side_effect = [
        ProcessResult(("pytest",), exit_code=0, stdout="1 passed, 1 skipped"),
        ProcessResult(("pytest",), exit_code=5),
    ]
    records = await measure(runtime, "new-task", ("pytest",), ("pytest regression",))
    assert [item["outcome"] for item in records] == ["skipped", "missing_results"]


def test_evidence_is_preserved_not_overwritten(tmp_path):
    path = write_evidence(tmp_path, {"validation_committed": False})
    assert json.loads(open(path, encoding="utf-8").read())["validation_committed"] is False
    with pytest.raises(FileExistsError):
        write_evidence(tmp_path, {"validation_committed": True})


def test_windows_paths_are_not_backslash_escaped(monkeypatch):
    monkeypatch.setattr("Sprout.evolution.replay_measurement.os.name", "nt")
    assert command_parts(r'python "tests\my test.py"') == ("python", r"tests\my test.py")


def test_empty_command_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        command_parts("  ")
