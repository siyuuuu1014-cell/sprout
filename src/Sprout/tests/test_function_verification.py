import base64
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from Sprout.evolution.function_provenance import function_provenance
from Sprout.execution.function_verification import FunctionVerificationRun, judge_actual
from Sprout.execution.models import ProcessResult
from Sprout.strategy.acceptance import AcceptanceCase
from Sprout.tests.test_verification_execution import prepare_run


@pytest.mark.parametrize("stdout,expected,passed", [
    ('{"b":2,"a":1}', {"a": 1, "b": 2}, True),
    ("true", 1, False), ("[1,2]", [1, 2], True),
    ('{"passed":true}', 1, False), ("null", None, True),
])
def test_host_compares_actual_not_pass_flag(stdout, expected, passed):
    assert judge_actual(ProcessResult(("docker",), exit_code=0, stdout=stdout), expected)[
        "passed"] is passed


@pytest.mark.parametrize("stdout", ["log\n1", "NaN", "1\n2", '"' + "a" * 65536 + '"'],
                         ids=["logs", "nonfinite", "multiple", "oversized"])
def test_invalid_actual_rejected(stdout):
    with pytest.raises(ValueError):
        judge_actual(ProcessResult(("docker",), exit_code=0, stdout=stdout), 1)


async def test_expected_not_sent_and_completed_cached(tmp_path):
    store, request, runtime, inspection, _ = await prepare_run(tmp_path)
    case = AcceptanceCase("normal", "app:run", (7,), {}, "hidden-oracle")
    async def run(task_id, command, **kwargs):
        if command[1] == "image":
            return inspection
        payload = json.loads(base64.b64decode(command[-1]))
        assert payload == {"target": "app:run", "args": [7], "kwargs": {}}
        assert "hidden-oracle" not in str(command)
        return ProcessResult(command, exit_code=0, stdout='"hidden-oracle"')
    runtime.run_task_process.side_effect = run
    runner = FunctionVerificationRun(store)
    kwargs = dict(task_id=request.task_id, original=request.source_root,
                  exported=request.input_root, image=request.image, case=case,
                  oracle_paths=("tests",))
    try:
        output = await runner.execute(runtime, **kwargs)
        assert output["host_oracle_passed"] is True
        assert output["validation_committed"] is False
        assert await runner.execute(runtime, **kwargs) == output
        assert runtime.run_task_process.call_count == 2
        result = SimpleNamespace(task_id=request.task_id, evidence_dir=str(tmp_path))
        report = {"acceptance_spec": {"cases": [{"id": case.id, "target": case.target,
                  "args": list(case.args), "kwargs": case.kwargs,
                  "expected_output": case.expected_output}]}, "function_records": [output]}
        before = (tmp_path / "metadata.db").read_bytes()
        assert function_provenance([result], [report])["verified"] is True
        assert (tmp_path / "metadata.db").read_bytes() == before
        # Matching forged summaries are still compared against saved raw actual stdout.
        output["observation"]["passed"] = False
        output["host_oracle_passed"] = False
        with sqlite3.connect(tmp_path / "metadata.db") as connection:
            connection.execute("UPDATE verification_executions SET result_json=?",
                               (json.dumps(output),))
        assert function_provenance([result], [report])["verified"] is False
        changed = await runner.execute(
            runtime, **{**kwargs, "case": replace(case, expected_output=9)})
        assert changed["reason"] == "function_request_changed"
        assert runtime.run_task_process.call_count == 2
    finally:
        store.close()


async def test_unknown_result_not_reexecuted(tmp_path):
    store, request, runtime, inspection, _ = await prepare_run(tmp_path)
    runtime.run_task_process.side_effect = [inspection, ProcessResult(("docker",), error="timeout")]
    runner = FunctionVerificationRun(store)
    kwargs = dict(task_id=request.task_id, original=request.source_root,
                  exported=request.input_root, image=request.image,
                  case=AcceptanceCase("normal", "app:run", (), {}, 1), oracle_paths=("tests",))
    try:
        assert (await runner.execute(runtime, **kwargs))["waiting"] is True
        assert (await runner.execute(runtime, **kwargs))["waiting"] is True
        assert runtime.run_task_process.call_count == 2
    finally:
        store.close()


async def test_oracle_files_not_mounted(tmp_path):
    store, request, runtime, _, _ = await prepare_run(tmp_path)
    (request.input_root / "tests").mkdir()
    try:
        with pytest.raises(ValueError, match="exclude frozen oracle"):
            await FunctionVerificationRun(store).execute(
                runtime, task_id=request.task_id, original=request.source_root,
                exported=request.input_root, image=request.image,
                case=AcceptanceCase("normal", "app:run", (), {}, 1), oracle_paths=("tests",))
        runtime.run_task_process.assert_not_called()
    finally:
        store.close()
