"""No business remediation: classify original output, never replace acceptance commands."""

import json
import subprocess
import sys

import pytest

from scripts.ci_support.acceptance_probe import acceptance_launch
from scripts.ci_support.diagnostics import project

COMMAND = ("python", "-m", "pytest", "-q", "tests/test_fixture.py::test_normal")
PRIVATE = "PRIVATE-user-key-and-prompt"


def diagnostic(output, **kwargs):
    return acceptance_launch(COMMAND, output, executed=True, passed=False, **kwargs)


def test_windows_reported_interpreter_mismatch_does_not_publish_paths():
    result = diagnostic(
        rf"[stderr]\nC:\Users\{PRIVATE}\base\python.exe: No module named pytest".replace(
            r"\n", "\n"
        ),
        expected_executable=rf"C:\Users\{PRIVATE}\venv\Scripts\python.exe",
    )
    assert result["reported_executable_matches_expected"] is False
    assert result["launcher_imports_pytest"] is False
    assert result["missing_module_category"] == "pytest"
    assert result["failure_phase"] == "before_pytest_startup"
    assert PRIVATE not in json.dumps(result)
    assert "C:" not in json.dumps(result)


def test_windows_path_comparison_is_case_and_separator_aware():
    result = diagnostic(
        "C:/VENV/Scripts/python.exe: No module named pytest\n",
        expected_executable=r"c:\venv\scripts\PYTHON.EXE",
    )
    assert result["reported_executable_matches_expected"] is True


@pytest.mark.parametrize(
    ("module", "category"),
    [
        ("pytest", "pytest"),
        ("src.app", "project_module"),
        ("private_dependency", "other_dependency"),
    ],
)
def test_collection_module_classification_does_not_expose_import_name(module, category):
    result = diagnostic(
        "ERROR collecting tests/test_fixture.py\n"
        f"ModuleNotFoundError: No module named '{module}'\n{PRIVATE}"
    )
    assert result["missing_module_category"] == category
    assert result["failure_phase"] == "collection"
    assert result["launcher_imports_pytest"] is True
    assert result["reported_executable_matches_expected"] is None
    assert PRIVATE not in json.dumps(result)
    assert "private_dependency" not in json.dumps(result)


def test_execution_failure_is_not_confused_with_pytest_startup_failure():
    result = diagnostic(
        "================ FAILURES ================\n"
        "ModuleNotFoundError: No module named 'src.export'"
    )
    assert result["failure_phase"] == "test_execution"
    assert result["launcher_imports_pytest"] is True
    assert result["reported_executable_matches_expected"] is None


def test_unknown_output_never_invents_interpreter_identity_or_module():
    result = diagnostic(PRIVATE)
    assert result["reported_executable_matches_expected"] is None
    assert result["launcher_imports_pytest"] is None
    assert result["missing_module_category"] == result["failure_phase"] == "unknown"
    assert PRIVATE not in json.dumps(result)


def test_passing_check_does_not_claim_outer_python_is_the_child():
    result = acceptance_launch(COMMAND, "1 passed", executed=True, passed=True)
    assert result["launcher_imports_pytest"] is True
    assert result["failure_phase"] == "not_failed"
    assert result["reported_executable_matches_expected"] is None


def test_unexecuted_check_and_relative_prefix_are_not_identity_evidence():
    text = "python: No module named pytest"
    assert diagnostic(text)["reported_executable_matches_expected"] is None
    result = acceptance_launch(COMMAND, text, executed=False, passed=False)
    assert result["launcher_imports_pytest"] is None


def test_real_synthetic_child_startup_error_is_parsed_without_a_second_probe():
    # Unit simulation only: -I -S deliberately excludes site packages. This is
    # NOT a measurement of the runtime's unchanged original acceptance process.
    child = subprocess.run(
        [sys.executable, "-I", "-S", "-m", "pytest"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert child.returncode != 0
    result = diagnostic(child.stderr, expected_executable=sys.executable)
    assert result["launcher_imports_pytest"] is False
    assert result["reported_executable_matches_expected"] is True
    assert result["failure_phase"] == "before_pytest_startup"
    assert sys.executable not in json.dumps(result)


def test_existing_projection_retains_new_fields_without_exporting_raw_output():
    report = {
        "verified": False,
        "checks": [
            {
                "name": "acceptance:normal",
                "command": COMMAND,
                "executed": True,
                "passed": False,
                "exit_code": 1,
                "output": f"C:\\{PRIVATE}\\python.exe: No module named pytest\n",
            }
        ],
    }
    summary = project("eval_C01", verification=report)
    result = summary["verification"]["checks"][0]["launch_diagnostic"]
    assert result["missing_module_category"] == "pytest"
    assert PRIVATE not in json.dumps(summary)
