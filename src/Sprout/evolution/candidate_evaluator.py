"""Combined candidate evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from Sprout.artifacts.models import GrowthCandidate
from Sprout.evolution.hard_gate import (
    GateChecks,
    HardGateEvaluator,
    HardGateResult,
)
from Sprout.evolution.utility import UtilityEvaluator, UtilityReport


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    passed: bool
    hard_gate: HardGateResult
    utility: UtilityReport
    pending: bool = False


class CandidateEvaluator:
    def __init__(
        self,
        *,
        hard_gate: HardGateEvaluator | None = None,
        utility: UtilityEvaluator | None = None,
        utility_threshold: float = 0.5,
    ) -> None:
        self._hard_gate = hard_gate or HardGateEvaluator()
        self._utility = utility or UtilityEvaluator()
        self._utility_threshold = utility_threshold

    def evaluate(
        self, candidate: GrowthCandidate, checks: GateChecks | None = None
    ) -> CandidateEvaluation:
        hard_gate = self._hard_gate.evaluate(candidate, checks)
        utility = self._utility.evaluate(candidate)
        missing = checks is None or checks.tests_passed is None or checks.regression_free is None
        known_gate = self._hard_gate.evaluate(
            candidate,
            GateChecks(
                True if checks is None or checks.tests_passed is None else checks.tests_passed,
                True
                if checks is None or checks.regression_free is None
                else checks.regression_free,
            ),
        )
        return CandidateEvaluation(
            passed=hard_gate.passed and utility.score >= self._utility_threshold,
            hard_gate=hard_gate,
            utility=utility,
            pending=missing and known_gate.passed and utility.score >= self._utility_threshold,
        )
