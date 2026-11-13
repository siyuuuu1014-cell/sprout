import base64
import json
import os
import subprocess
import sys

import pytest

from Sprout.execution.function_verification import judge_case
from Sprout.execution.isolated_case_contract import case_command
from Sprout.execution.models import ProcessResult
from Sprout.strategy.acceptance import AcceptanceSpec


@pytest.mark.parametrize(("source", "patch", "passed"), [
    ("return sum(values)", {"expected_output": 3}, True),
    ("return 9", {"expected_output": 3}, False),
    ("raise ValueError('invalid')", {"expected_exception": "ValueError"}, True),
    ("raise TypeError('invalid')", {"expected_exception": "ValueError"}, False),
    ("return None", {"expected_exception": "ValueError"}, False),
    ("raise ValueError('invalid')", {"expected_output": 3}, False),
])
def test_real_extended_worker_round_trip(tmp_path, source, patch, passed):
    (tmp_path / "fixture.py").write_text(
        "from types import GeneratorType\n"
        "def check(values):\n"
        "    assert isinstance(values, GeneratorType)\n"
        f"    {source}\n", encoding="utf8",
    )
    case = AcceptanceSpec.from_payload({"cases": [{
        "id": "case", "target": "fixture:check", "args": [[1, 2]],
        "iterable_args": [{"argument": 0, "kind": "generator"}],
        **patch,
    }]}).cases[0]
    command = case_command("acceptance", case, "")
    assert json.loads(base64.b64decode(command[-1])).keys() == {
        "target", "args", "kwargs", "iterable_args",
    }
    # Execute the exact worker body in a separate process against the test fixture.
    body = command[-2].replace('"/workspace"', repr(str(tmp_path)))
    result = subprocess.run([sys.executable, "-I", "-c", body, command[-1]],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stderr
    process = ProcessResult(command, exit_code=result.returncode, stdout=result.stdout)
    assert judge_case(process, case)["passed"] is passed


@pytest.mark.parametrize("patch", [
    {"expected_exception": "ValueError", "expected_output": None},
    {"expected_exception": "fixture.CustomError"},
    {"expected_output": 3, "iterable_args": [{"argument": 4, "kind": "generator"}]},
    {"expected_output": 3, "iterable_args": [{"argument": 0, "kind": "expression"}]},
    {"expected_output": 3, "iterable_args": [{"argument": 0, "kind": "generator"}] * 2},
])
def test_rejects_invalid_extensions(patch):
    with pytest.raises(ValueError):
        AcceptanceSpec.from_payload({"cases": [{
            "id": "case", "target": "fixture:check", "args": [[1, 2]], **patch,
        }]})


def test_round_trip_keeps_v1_payload_unchanged():
    payload = {"version": 1, "cases": [{
        "id": "normal", "target": "fixture:check", "args": [1],
        "kwargs": {}, "expected_output": 2, "category": "normal",
    }]}
    assert AcceptanceSpec.from_payload(json.loads(json.dumps(payload))).to_payload() == payload



def test_keyword_iterator_and_import_error_are_distinct(tmp_path):
    from Sprout.execution.isolated_case_contract import public_demo

    fixture = tmp_path / "fixture.py"
    case = AcceptanceSpec.from_payload({"version": 2, "cases": [{
        "id": "example", "target": "fixture:check", "kwargs": {"values": [1, 2]},
        "iterable_args": [{"argument": "values", "kind": "iterator"}],
        "expected_exception": "ValueError",
    }]}).cases[0]
    body = public_demo(case).replace('"/workspace"', repr(str(tmp_path)))
    fixture.write_text(
        "def check(*, values):\n"
        "    assert iter(values) is values\n"
        "    assert next(values) == 1\n"
        "    raise ValueError('from target')\n",
        encoding="utf8",
    )
    argv = [sys.executable, "-I", "-c", body]
    result = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert judge_case(ProcessResult(tuple(argv), exit_code=result.returncode,
                                    stdout=result.stdout), case)["passed"]
    fixture.write_text("raise ValueError('from import')\n", encoding="utf8")
    result = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=10)
    with pytest.raises(ValueError, match="Actual output unavailable"):
        judge_case(ProcessResult(tuple(argv), exit_code=result.returncode,
                                 stdout=result.stdout), case)


def test_filename_free_feature_plan_keeps_documents_and_tests_readonly():
    from Sprout.cli.filesystem_preparation import plan_files
    from Sprout.intents import detect_task_hint

    instruction = (
        "完善金额计算功能，让合法的生成器和迭代器输入得到正确金额，"
        "非法输入应抛出 ValueError，保留现有规格、说明和测试文件，运行测试。"
    )
    paths = ("totals.py", "SPEC.md", "README.md", "tests/test_totals.py")
    assert detect_task_hint(instruction)
    plan = plan_files(paths, instruction)
    assert set(plan["reads"]) == set(paths)
    assert plan["writes"] == ("totals.py",)
    assert plan["execution"] == ("run_tests",)
