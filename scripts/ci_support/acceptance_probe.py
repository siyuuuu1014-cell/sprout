"""Passive test-only interpretation of original acceptance subprocess output.

Never launch a probe or alter commands, environment, sys.path or authorization.
CPython's -m startup error can report its executable even before pytest loads.
Paths/module names are compared in memory and discarded, not redacted later.
"""

import ntpath
import posixpath
import re
import sys

_MODULE = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
_STARTUP = re.compile(
    rf"(?m)^(?P<exe>[^\r\n]+): No module named ['\"]?(?P<module>{_MODULE})['\"]?\s*$"
)
_MISSING = re.compile(rf"No module named ['\"]?(?P<module>{_MODULE})['\"]?")


def _normalized_executable(value):
    if not isinstance(value, str):
        return None
    if ntpath.splitdrive(value)[0] and ntpath.isabs(value):
        return ntpath.normcase(ntpath.normpath(value))
    if posixpath.isabs(value):
        return posixpath.normpath(value)
    return None


def acceptance_launch(command, output, *, executed, passed, expected_executable=None):
    """Project observable facts only. Missing evidence is None/unknown, not a match."""
    result = {
        "evidence_source": "original_acceptance_check",
        "reported_executable_matches_expected": None,
        "launcher_imports_pytest": None,
        "missing_module_category": "unknown",
        "failure_phase": "unknown",
    }
    if (
        not isinstance(command, (list, tuple))
        or list(command[1:3]) != ["-m", "pytest"]
        or executed is not True
    ):
        return result
    text = output if isinstance(output, str) else ""
    startup = _STARTUP.search(text)
    if startup:
        actual = _normalized_executable(startup["exe"])
        expected = _normalized_executable(expected_executable or sys.executable)
        if actual is not None:
            result["reported_executable_matches_expected"] = (
                actual == expected if expected is not None else None
            )
            result["failure_phase"] = "before_pytest_startup"
            if startup["module"] == "pytest":
                result["launcher_imports_pytest"] = False
    if passed is True:
        result.update(launcher_imports_pytest=True, failure_phase="not_failed")
    elif result["failure_phase"] == "unknown":
        if re.search(r"\bERROR collecting\b", text):
            result.update(launcher_imports_pytest=True, failure_phase="collection")
        elif re.search(r"={2,}\s*FAILURES\s*={2,}", text):
            result.update(launcher_imports_pytest=True, failure_phase="test_execution")
    missing = _MISSING.search(text)
    if missing:
        # These first-party diagnostic fixtures use src.* targets. Do not guess
        # arbitrary project module names or export the captured import string.
        root = missing["module"].split(".", 1)[0]
        result["missing_module_category"] = (
            "pytest"
            if root == "pytest"
            else "project_module"
            if root == "src"
            else "other_dependency"
        )
    return result
