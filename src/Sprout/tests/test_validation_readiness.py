from copy import deepcopy

import pytest

from Sprout.evolution.validation_readiness import measurement_readiness


def complete():
    return dict(binding_blockers=[], condition_blockers=[], execution_blockers=[],
                pytest_outcomes=[True, True],
                functions={"verified": True, "observed_passed": [True, True], "blockers": []},
                regressions={"verified": True, "observed_passed": [True, True], "blockers": []})


def test_complete_observations_never_authorize_commit():
    report = measurement_readiness(**complete())
    assert report["observation_gates_passed"] is True
    assert report["measurement_blockers"] == []
    assert report["validation_blockers"] == ["trusted_measurement_handoff_not_established"]
    assert report["validation_authorized"] is False
    assert report["validation_committed"] is False


@pytest.mark.parametrize("group", ["functions", "regressions"])
@pytest.mark.parametrize("outcomes", [[True, False], [], [True], [1, True], None])
def test_missing_or_failed_pair_blocks(group, outcomes):
    inputs = complete()
    inputs[group]["observed_passed"] = outcomes
    before = deepcopy(inputs)
    report = measurement_readiness(**inputs)
    assert report["observation_gates_passed"] is False
    assert report["measurement_blockers"]
    assert inputs == before


@pytest.mark.parametrize("group", ["binding_blockers", "condition_blockers", "execution_blockers"])
def test_any_binding_failure_blocks(group):
    inputs = complete()
    inputs[group] = ["changed_evidence"]
    assert "changed_evidence" in measurement_readiness(**inputs)["validation_blockers"]


def test_pytest_failures_are_explicit_blockers():
    inputs = complete()
    inputs["pytest_outcomes"] = [True, False]
    assert "paired_pytest_observations_not_all_passed" in measurement_readiness(
        **inputs)["validation_blockers"]
