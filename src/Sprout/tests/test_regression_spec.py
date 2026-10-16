import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.evolution.function_provenance import function_provenance
from Sprout.evolution.regression_spec import freeze, frozen, load_operator_spec
from Sprout.evolution.replay_functions import measure_functions
from Sprout.execution.function_verification import FunctionVerificationRun
from Sprout.execution.models import ProcessResult
from Sprout.strategy.acceptance import AcceptanceCase
from Sprout.tests.test_replay_functions import fixture
from Sprout.tests.test_verification_execution import prepare_run


def test_explicit_spec_frozen_exclusively_and_tampering_rejected(tmp_path):
    _, kwargs = fixture(tmp_path)
    spec = kwargs["acceptance_spec"]
    directory = kwargs["directory"]
    fingerprint = freeze(directory, spec)
    assert frozen(directory, fingerprint) == spec
    with pytest.raises(FileExistsError):
        freeze(directory, spec)
    changed = json.loads((directory / "regression-spec.json").read_text())
    changed["cases"][0]["expected_output"] = 99
    (directory / "regression-spec.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        frozen(directory, fingerprint)


async def test_missing_regression_waits_without_running(tmp_path, monkeypatch):
    runtime, kwargs = fixture(tmp_path)
    execute = AsyncMock()
    monkeypatch.setattr(
        "Sprout.evolution.replay_functions.FunctionVerificationRun.execute", execute)
    kwargs["acceptance_spec"] = None
    output = await measure_functions(runtime, "task", **kwargs, purpose="regression")
    assert output[0]["waiting"]
    execute.assert_not_called()


async def test_regression_uses_frozen_spec_not_acceptance(tmp_path, monkeypatch):
    runtime, kwargs = fixture(tmp_path)
    spec = json.loads(json.dumps(kwargs["acceptance_spec"]))
    spec["cases"][0]["args"] = [3]
    spec["cases"][0]["expected_output"] = 6
    fingerprint = freeze(kwargs["directory"], spec)
    execute = AsyncMock(return_value={"waiting": False, "input_unchanged": True})
    monkeypatch.setattr(
        "Sprout.evolution.replay_functions.FunctionVerificationRun.execute", execute)
    output = await measure_functions(runtime, "task", **kwargs, purpose="regression",
                                     regression_fingerprint=fingerprint)
    assert not output[0]["waiting"]
    assert execute.call_args.kwargs["purpose"] == "regression"
    assert execute.call_args.kwargs["case"].args == (3,)
    assert execute.call_args.kwargs["case"].expected_output == 6
    assert execute.call_args.kwargs["exported"].name == "regression-input"
    assert not (kwargs["directory"] / "regression-input/tests").exists()


def test_operator_json_size_and_invalid_data_rejected(tmp_path):
    path = tmp_path / "oracle.json"
    path.write_text('{"cases":[]}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_operator_spec(path)
    path.write_text("a" * 65537, encoding="utf-8")
    with pytest.raises(ValueError, match="64 KiB"):
        load_operator_spec(path)


async def test_regression_record_has_independent_identity_and_read_only_rejudgment(tmp_path):
    store, request, runtime, inspection, _ = await prepare_run(tmp_path)
    case = AcceptanceCase("same", "app:run", (), {}, 4)
    spec = {"cases": [{"id": "same", "target": "app:run", "expected_output": 4}]}
    fingerprint = freeze(tmp_path, spec)
    async def run(task_id, command, **kwargs):
        return inspection if command[1] == "image" else ProcessResult(
            command, exit_code=0, stdout="4")
    runtime.run_task_process.side_effect = run
    kwargs = dict(task_id=request.task_id, original=request.source_root,
                  exported=request.input_root, image=request.image, case=case,
                  oracle_paths=("tests",))
    try:
        runner = FunctionVerificationRun(store)
        acceptance = await runner.execute(runtime, **kwargs)
        regression = await runner.execute(runtime, **kwargs, purpose="regression")
        assert acceptance["execution_id"] != regression["execution_id"]
        result = SimpleNamespace(task_id=request.task_id, evidence_dir=str(tmp_path))
        report = {"regression_fingerprint": fingerprint, "regression_records": [regression]}
        before = (tmp_path / "metadata.db").read_bytes()
        verdict = function_provenance([result], [report], purpose="regression")
        assert verdict == {"verified": True, "observed_passed": [True], "blockers": []}
        assert (tmp_path / "metadata.db").read_bytes() == before
        report["regression_records"] = [acceptance]
        assert function_provenance([result], [report], purpose="regression")["verified"] is False
    finally:
        store.close()
