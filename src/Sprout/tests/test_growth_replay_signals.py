"""Collection heuristics must not impersonate independent verification."""

import pytest

from Sprout.evolution.replay import ReplayEvaluator
from Sprout.trajectory.models import Trajectory, TrajectoryEvent


@pytest.mark.parametrize("name", ["test.started", "test.passed", "test.skipped", "test.completed"])
def test_positive_event_names_do_not_prove_measurement(name):
    trajectory = Trajectory(events=(TrajectoryEvent(name=name),))
    checks = ReplayEvaluator.checks(trajectory)
    assert checks.tests_passed is None
    assert checks.regression_free is None


@pytest.mark.parametrize("name", ["test.failed", "evaluation.failed"])
def test_failure_signals_still_block(name):
    checks = ReplayEvaluator.checks(Trajectory(events=(TrajectoryEvent(name=name),)))
    assert checks.tests_passed is False
