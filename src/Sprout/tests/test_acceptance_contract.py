"""Concrete requirement tests must reject incomplete generated code."""

import json

import pytest

from Sprout.execution.acceptance import acceptance_commands, render_acceptance_tests
from Sprout.strategy.acceptance import AcceptanceSpec


def test_acceptance_contract_round_trips_inputs_and_outputs() -> None:
    payload = {"cases": [{
        "id": "normal", "target": "sample:echo", "args": ["hi"],
        "kwargs": {}, "expected_output": "hi", "category": "normal",
    }]}
    spec = AcceptanceSpec.from_payload(payload)
    assert AcceptanceSpec.from_payload(json.loads(json.dumps(spec.to_payload()))) == spec
    assert acceptance_commands(spec, "tests/test_io.py")[0][-1] == "tests/test_io.py::test_normal"


@pytest.mark.parametrize("payload", [
    {}, {"cases": []}, {"cases": [{"id": "x", "target": "sample:echo"}]},
    {"cases": [{"id": "x", "target": "invalid target", "expected_output": None}]},
])
def test_acceptance_contract_refuses_missing_or_ambiguous_oracles(payload) -> None:
    with pytest.raises(ValueError):
        AcceptanceSpec.from_payload(payload)


def test_generated_assertion_rejects_empty_implementation(monkeypatch) -> None:
    from types import ModuleType

    module = ModuleType("acceptance_example")
    module.export = lambda users: ""
    monkeypatch.setitem(__import__("sys").modules, module.__name__, module)
    spec = AcceptanceSpec.from_payload({"cases": [{
        "id": "export", "target": "acceptance_example:export",
        "args": [[{"id": 1}]], "expected_output": "id\n1\n",
    }]})
    namespace = {"__file__": "/repo/tests/test_case.py"}
    exec(compile(render_acceptance_tests(spec), "generated_test.py", "exec"), namespace)
    with pytest.raises(AssertionError):
        namespace["test_export"]()
    module.export = lambda users: "id\n1\n"
    namespace["test_export"]()
