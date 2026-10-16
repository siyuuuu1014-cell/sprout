import asyncio
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.evolution.replay_functions import measure_functions
from Sprout.evolution.replay_oracle import snapshot
from Sprout.execution.acceptance import render_acceptance_demo, render_acceptance_tests
from Sprout.execution.models import ProcessResult
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import Task, TaskStatus


def fixture(tmp_path):
    repo, original, directory = (tmp_path / name for name in ("repo", "original", "evidence"))
    for path in (repo, original, directory):
        path.mkdir()
    (repo / "tests").mkdir()
    (repo / "examples").mkdir()
    spec = AcceptanceSpec.from_payload({"cases": [
        {"id": "ok", "target": "app:run", "args": [2], "expected_output": 4}]})
    (repo / "app.py").write_text("def run(value):\n    return value * 2\n", encoding="utf-8")
    (repo / "tests/test_oracle.py").write_text(
        render_acceptance_tests(spec), encoding="utf-8", newline="\n")
    (repo / "examples/demo.py").write_text(
        render_acceptance_demo(spec), encoding="utf-8", newline="\n")
    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=AsyncMock()))
    return runtime, dict(directory=directory, repo=repo, original=original,
                         image="sha256:" + "a" * 64, oracle_paths=("tests",),
                         frozen=snapshot(repo, ("tests",)), acceptance_spec=spec.to_payload())


async def test_separate_export_excludes_tests_and_generated_demo(tmp_path, monkeypatch):
    runtime, kwargs = fixture(tmp_path)
    execute = AsyncMock(return_value={"waiting": False, "input_unchanged": True})
    monkeypatch.setattr(
        "Sprout.evolution.replay_functions.FunctionVerificationRun.execute", execute)
    output = await measure_functions(runtime, "task", **kwargs)
    assert len(output) == 1 and not output[0]["waiting"]
    exported = kwargs["directory"] / "function-input"
    assert (exported / "app.py").is_file()
    assert not (exported / "tests").exists()
    assert not (exported / "examples/demo.py").exists()
    assert (kwargs["repo"] / "tests/test_oracle.py").is_file()
    (exported / "app.py").write_text("changed", encoding="utf-8")
    output = await measure_functions(runtime, "task", **kwargs)
    assert output[0]["waiting"]
    assert execute.call_count == 1


@pytest.mark.parametrize("changed", ["spec", "oracle", "partial"])
async def test_missing_or_changed_binding_blocks_before_execution(tmp_path, monkeypatch, changed):
    runtime, kwargs = fixture(tmp_path)
    execute = AsyncMock()
    monkeypatch.setattr(
        "Sprout.evolution.replay_functions.FunctionVerificationRun.execute", execute)
    if changed == "spec":
        kwargs["acceptance_spec"]["cases"][0]["expected_output"] = 99
    elif changed == "oracle":
        (kwargs["repo"] / "tests/test_oracle.py").write_text("changed", encoding="utf-8")
    else:
        (kwargs["directory"] / "function-input").mkdir()
    assert (await measure_functions(runtime, "task", **kwargs))[0]["waiting"]
    execute.assert_not_called()


async def test_absent_spec_does_not_invent_acceptance(tmp_path):
    runtime, kwargs = fixture(tmp_path)
    kwargs["acceptance_spec"] = None
    assert await measure_functions(runtime, "task", **kwargs) == []


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
async def test_source_drift_never_reuses_old_measurement(tmp_path, monkeypatch, change):
    runtime, kwargs = fixture(tmp_path)
    execute = AsyncMock(return_value={"waiting": False, "input_unchanged": True})
    monkeypatch.setattr(
        "Sprout.evolution.replay_functions.FunctionVerificationRun.execute", execute)
    assert not (await measure_functions(runtime, "task", **kwargs))[0]["waiting"]
    original_export = (kwargs["directory"] / "function-input/app.py").read_bytes()
    if change == "edit":
        (kwargs["repo"] / "app.py").write_text("changed", encoding="utf-8")
    elif change == "add":
        (kwargs["repo"] / "extra.py").write_text("extra", encoding="utf-8")
    else:
        (kwargs["repo"] / "app.py").unlink()
    assert (await measure_functions(runtime, "task", **kwargs))[0]["waiting"]
    execute.assert_awaited_once()
    assert (kwargs["directory"] / "function-input/app.py").read_bytes() == original_export


@pytest.mark.skipif(not os.getenv("SPROUT_VERIFICATION_TEST_IMAGE"),
                    reason="explicit pinned verification image required")
async def test_real_function_container_and_durable_cache(tmp_path):
    _, kwargs = fixture(tmp_path)
    from Sprout.evolution.regression_spec import freeze

    kwargs["regression_fingerprint"] = freeze(kwargs["directory"], kwargs["acceptance_spec"])
    kwargs["image"] = os.environ["SPROUT_VERIFICATION_TEST_IMAGE"]
    (kwargs["repo"] / "app.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "def run(value):\n"
        "    assert os.getuid() != 0\n"
        "    assert not Path('/workspace/tests').exists()\n"
        "    assert not Path('/workspace/examples/demo.py').exists()\n"
        "    assert not Path('/var/run/docker.sock').exists()\n"
        "    return value * 2\n", encoding="utf-8", newline="\n")
    store = open_metadata_store(str(kwargs["directory"] / "metadata.db"))
    task = Task(status=TaskStatus.COMPLETED, metadata={"execution_mode": "code"})
    await store.save_task(task)
    names, calls = [], []

    async def run(task_id, command, *, timeout_seconds=None, max_output_bytes=None):
        assert task_id == task.id and command[0] == "docker"
        assert command[1:3] == ("image", "inspect") or command[1] == "run"
        calls.append(command)
        if command[1] == "run":
            names.append(command[command.index("--name") + 1])
        result = await asyncio.to_thread(
            subprocess.run, command, capture_output=True, text=True, timeout=timeout_seconds)
        return ProcessResult(command, exit_code=result.returncode,
                             stdout=result.stdout, stderr=result.stderr)

    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=store), run_task_process=run)
    try:
        result = await measure_functions(runtime, task.id, **kwargs)
        assert len(result) == 1 and result[0]["host_oracle_passed"] is True
        assert result[0]["validation_committed"] is False
        assert await measure_functions(runtime, task.id, **kwargs) == result
        assert len(calls) == 2
        regressions = await measure_functions(runtime, task.id, **kwargs, purpose="regression")
        assert regressions[0]["host_oracle_passed"] is True
        assert regressions[0]["purpose"] == "regression"
        assert regressions[0]["execution_id"] != result[0]["execution_id"]
        cached_regressions = await measure_functions(
            runtime, task.id, **kwargs, purpose="regression")
        assert cached_regressions == regressions
        assert len(calls) == 4
        (kwargs["directory"] / "function-observation.json").write_text(
            json.dumps({"acceptance": result, "regression": regressions}, indent=2),
            encoding="utf-8")
    finally:
        store.close()
        for name in names:
            inspection = subprocess.run(["docker", "inspect", name], capture_output=True,
                                        text=True, timeout=15)
            if inspection.returncode == 0:
                state = json.loads(inspection.stdout)[0]
                if (state["Config"]["Labels"].get("sprout.role") == "independent-verification"
                        and not state["State"]["Running"]):
                    subprocess.run(["docker", "rm", name], capture_output=True,
                                   check=True, timeout=15)
