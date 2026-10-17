"""Local acceptance evidence must remain tied to its server execution environment."""

import sys
from types import ModuleType

import pytest

from Sprout.execution import acceptance
from Sprout.strategy.acceptance import AcceptanceSpec


def test_local_acceptance_does_not_search_path_for_python():
    spec = AcceptanceSpec.from_payload({"cases": [{
        "id": "normal", "target": "sample:echo", "args": ["hi"], "expected_output": "hi",
    }]})
    assert acceptance.acceptance_commands(spec, "tests/test_io.py")[0][0] == sys.executable


def test_existing_new_function_baseline_fails_before_the_oracle_assertion(monkeypatch):
    """Observed incompatibility, not permission to classify this as an assertion failure."""
    module = ModuleType("binding_baseline_example")
    monkeypatch.setitem(__import__("sys").modules, module.__name__, module)
    spec = AcceptanceSpec.from_payload({"cases": [{
        "id": "normal", "target": "binding_baseline_example:export_users",
        "args": [[]], "expected_output": "",
    }]})
    namespace = {"__file__": "/repo/tests/test_case.py"}
    exec(compile(acceptance.render_acceptance_tests(spec), "generated_test.py", "exec"), namespace)
    with pytest.raises(AttributeError, match="export_users"):
        namespace["test_normal"]()
