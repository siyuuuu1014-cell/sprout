"""Controlled launcher observation with real Python tracebacks; no child execution."""
import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution import acceptance
from Sprout.execution.models import ProcessResult
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.tests.test_r04_f2_execution_contract import execution as execution
from Sprout.tests.test_r04_f2_execution_contract import rig as rig


@pytest.mark.parametrize("scenario,expected", [
    ("target", "target_exception"), ("wrong-args", "unknown"),
    ("import", "unknown"), ("framework", "unknown"), ("setup", "unknown"),
    ("changed-test", "unknown"), ("changed-target", "unknown"),
    ("other-exception", "unknown"), ("collection", "collection_error"),
    ("runner", "runner_error"), ("other-module", "unknown"),
])
def test_launcher_typeerror_traceback(tmp_path, monkeypatch, capsys, scenario, expected):
    name = "f2_exception_fixture"
    source = "def run(value):\n    return len(value)\n"
    if scenario == "import":
        source = "raise TypeError('import')\n"
    if scenario == "other-exception":
        source = "def run(value):\n    raise RuntimeError('target')\n"
    if scenario == "other-module":
        source = "from f2_exception_helper import run\n"
        (tmp_path / "f2_exception_helper.py").write_text(
            "def run(value):\n    return len(value)\n", encoding="utf-8")
    (tmp_path / (name + ".py")).write_text(source, encoding="utf-8")
    spec = AcceptanceSpec.from_payload({"cases": [dict(id="case", target=name + ":run",
        args=[] if scenario == "wrong-args" else [None], expected_output={})]})
    text = acceptance.render_acceptance_tests(spec)
    (tmp_path / "tests").mkdir()
    test = tmp_path / "tests/test_frozen.py"
    test.write_text(text, encoding="utf-8")
    binding = {"version": 1, "domain": "sprout.local-acceptance", "fingerprint": "0" * 64}
    payload = json.loads(acceptance.local_acceptance_command(
        spec, "tests/test_frozen.py", "case", binding)[-1])
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(sys, "argv", ["launcher", json.dumps(payload)])
    monkeypatch.setattr(acceptance, "_local_identity", lambda protocol: binding)
    importlib.invalidate_caches()

    def main(argv, plugins):
        observer = plugins[0]
        item = SimpleNamespace(path=test, name="test_case")
        observer.pytest_collection_finish(SimpleNamespace(testsfailed=0, items=[item]))
        namespace = {"__file__": str(test)}
        exec(compile(text, str(test), "exec"), namespace)
        try:
            if scenario == "framework":
                raise TypeError("framework")
            namespace["test_case"]()
        except Exception:
            excinfo = pytest.ExceptionInfo.from_current()
        if scenario == "changed-test":
            test.write_text(text + "# changed\n", encoding="utf-8")
        if scenario == "changed-target":
            (tmp_path / (name + ".py")).write_text(source + "# changed\n", encoding="utf-8")
        observer.pytest_runtest_makereport(item, SimpleNamespace(
            when="setup" if scenario == "setup" else "call", excinfo=excinfo))
        return 2 if scenario == "collection" else 3 if scenario == "runner" else 1
    monkeypatch.setattr(pytest, "main", main)
    try:
        with pytest.raises(SystemExit) as exit_info:
            acceptance._local_acceptance_main()
        assert exit_info.value.code == (2 if scenario == "collection" else
                                       3 if scenario == "runner" else 1)
        evidence = json.loads(capsys.readouterr().out.split("SPROUT_LOCAL_ACCEPTANCE_V1:")[1])
        assert evidence["category"] == expected
    finally:
        sys.modules.pop(name, None)
        sys.modules.pop("f2_exception_helper", None)


@pytest.mark.parametrize("change,expected", [("none", "target_exception"),
    ("exit-zero", "unknown"), ("exit-two", "unknown"), ("binding", "unknown"),
    ("identity", "unknown"), ("executed", "unknown"), ("collected", "unknown"),
    ("duplicate", "unknown"), ("not-started", "not_executed")])
def test_target_exception_consumer_rejects_invalid_evidence(monkeypatch, change, expected):
    binding = {"version": 1, "domain": "sprout.local-acceptance", "fingerprint": "0" * 64}
    monkeypatch.setattr(acceptance, "local_executor_applicability", lambda _: "match")
    evidence = dict(version=1, binding=binding, category="target_exception",
                    executed=True, collected=True, identity_matches=True)
    if change == "binding":
        evidence["binding"] = {}
    if change in {"identity", "executed", "collected"}:
        evidence["identity_matches" if change == "identity" else change] = False
    output = "SPROUT_LOCAL_ACCEPTANCE_V1:" + json.dumps(evidence) + "\n"
    result = ProcessResult(command=(), allowed=True, started=change != "not-started",
        exit_code=0 if change == "exit-zero" else 2 if change == "exit-two" else 1,
        stdout=output * (2 if change == "duplicate" else 1))
    assert acceptance.local_acceptance_evidence(result, binding) == expected


@pytest.mark.parametrize("category,valid", [("target_exception", True), ("unknown", False),
    ("collection_error", False), ("runner_error", False)])
async def test_host_baseline_known_red_is_not_a_pass(execution, category, valid):
    from Sprout.runtime.nodes import NodeExecutor

    e = execution
    binding = acceptance.local_executor_binding()
    evidence = dict(version=1, binding=binding, category=category,
                    executed=True, collected=True, identity_matches=True)
    response = ProcessResult(command=(), allowed=True, started=True, exit_code=1,
        stdout="SPROUT_LOCAL_ACCEPTANCE_V1:" + json.dumps(evidence))
    run = AsyncMock(return_value=response)
    executor = SimpleNamespace(_check_model_publication=AsyncMock(),
        _file_broker=e.rig.runtime._file_broker, _local_acceptance_run=run,
        _acceptance_wait=NodeExecutor._acceptance_wait)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node) as scope:
        scope.resource_receipts = tuple({"path": path, **resource}
            for path, resource in e.frozen["resources"].items())
        result = await NodeExecutor._host_test_design(executor, e.rig.node,
            e.rig.workspace, e.rig.task, e.sandbox)
    assert result["local_baseline_valid"] is valid
    assert result["baseline_failed"] is True
    assert result["baseline"][0]["exit_code"] == 1
    assert result["baseline"][0]["category"] == (category if valid else "unknown")
    assert result["waiting"] is (not valid)
    assert run.await_count == len(e.spec.cases)
