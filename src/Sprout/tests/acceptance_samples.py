"""Fixed developer exam registry; no production task or candidate registration."""

import shlex

from Sprout.tests import cart_acceptance_fixture as cart

ADDITION_INSTRUCTION = (
    "Fix app.add to implement integer addition. Inspect the workspace, "
    "add test_generated.py with positive, negative and zero unit tests, "
    "and run pytest through isolated_coding_command. "
    "Do not access the network or modify frozen oracles.")


def sample_definition(name):
    if name == "cart":
        return {"instruction": cart.INSTRUCTION, "sources": dict(cart.SOURCE_FILES),
            "required_paths": ["cart.py", "discounts.py", "checkout.py", "tests/test_generated.py"],
            "acceptance": cart.acceptance_spec(), "regression": cart.regression_spec(),
            "verification_commands": [{"kind": "cart-properties", "command": shlex.join(
                ["python", "-I", "-c", cart.PROPERTY_PROBE])}]}
    if name != "addition":
        raise ValueError("Unknown acceptance sample")
    return {"instruction": ADDITION_INSTRUCTION,
        "sources": {"app.py": "def add(a, b):\n    return 0\n",
                    "test_smoke.py": "from app import add\ndef test_add():\n"
                                     "    assert add(2, 3) == 5\n"},
        "required_paths": ["app.py", "test_generated.py"],
        "acceptance": {"cases": [{"id": "positive", "target": "app:add", "args": [2, 3],
                                   "expected_output": 5}]},
        "regression": {"cases": [{"id": "negative", "target": "app:add", "args": [-4, 1],
                                   "expected_output": -3},
                                  {"id": "zero", "target": "app:add", "args": [0, 0],
                                   "expected_output": 0}]}, "verification_commands": []}


def check_sample_task(name, task):
    sample = sample_definition(name)
    if (task.instruction != sample["instruction"]
            or task.metadata.get("required_paths") != sample["required_paths"]
            or task.metadata.get("verification_commands", []) != sample["verification_commands"]):
        raise ValueError("Sample must match persisted task requirements and verification commands")
    return sample
