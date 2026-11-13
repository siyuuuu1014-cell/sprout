import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.acceptance_producer import checked_bundle, produce
from Sprout.tests.test_acceptance_producer import inputs


@pytest.mark.parametrize(("missing", "reason"), [
    (None, ""),
    ("invalid", "acceptance_invalid_case_required"),
    ("invalid_exception", "acceptance_invalid_exception_required"),
    ("wrong_exception", "acceptance_expected_exception_missing"),
    ("generator", "acceptance_nonempty_generator_required"),
    ("iterator", "acceptance_nonempty_iterator_required"),
    ("empty_iterator", "acceptance_nonempty_iterator_required"),
    ("no_empty_boundary", "acceptance_empty_boundary_required"),
    ("no_invalid_generator", "acceptance_invalid_generator_required"),
])
async def test_git_freeze_requires_host_checked_semantic_coverage(missing, reason):
    entries, plan, design = inputs()
    normal, boundary = design["acceptance"]["cases"]
    boundary["args"] = [[]]
    normal["args"] = [[3]]
    normal["iterable_args"] = [{"argument": 0, "kind": "generator"}]
    iterator = {**normal, "id": "iter_case",
                "iterable_args": [{"argument": 0, "kind": "iterator"}]}
    invalid = {key: val for key, val in normal.items() if key != "expected_output"}
    invalid.update(id="bad_input", category="invalid", expected_exception="ValueError")
    cases = [normal, boundary, iterator, invalid]
    if missing == "invalid":
        cases.remove(invalid)
    elif missing == "invalid_exception":
        invalid.pop("expected_exception")
        invalid["expected_output"] = None
    elif missing == "wrong_exception":
        invalid["expected_exception"] = "TypeError"
    elif missing in {"generator", "iterator"}:
        (normal if missing == "generator" else iterator).pop("iterable_args")
    elif missing == "empty_iterator":
        iterator["args"] = [[]]
    elif missing == "no_empty_boundary":
        boundary["args"] = [1]
    elif missing == "no_invalid_generator":
        invalid.pop("iterable_args")
    design["acceptance"]["cases"] = cases
    model = SimpleNamespace(chat=AsyncMock(return_value=SimpleNamespace(text=json.dumps(design))))
    requirement = (
        "Support generator and iterator input. Empty input is the boundary. "
        "An invalid generator raises ValueError."
    )
    if missing:
        with pytest.raises(ValueError, match=reason):
            await produce(model, requirement, plan, entries, require_git_coverage=True)
        assert model.chat.await_count == 2
    else:
        bundle = await produce(model, requirement, plan, entries, require_git_coverage=True)
        acceptance, regression = checked_bundle(bundle)
        assert acceptance.version == 2
        assert regression.version == 1
        model.chat.assert_awaited_once()
