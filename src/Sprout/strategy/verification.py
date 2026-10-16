"""Generate verification criteria from target-project command evidence."""

from Sprout.strategy.models import (
    ImpactReport,
    VerificationCriterion,
    VerificationKind,
    VerificationPlan,
)
from Sprout.strategy.project_profile import ProjectProfile


class VerificationPlanner:
    def build(self, impact: ImpactReport, *, profile: ProjectProfile | None = None
              ) -> VerificationPlan:
        profile = profile or ProjectProfile()
        criteria = []
        for command in profile.test_commands:
            criteria.append(VerificationCriterion(
                VerificationKind.CODE, "regression", command,
                "focused and regression tests pass"))
            if impact.interfaces:
                criteria.append(VerificationCriterion(
                    VerificationKind.API, "project_suite", command,
                    "project suite passes; requirement-specific API cases are still required"))
            if impact.databases:
                criteria.append(VerificationCriterion(
                    VerificationKind.DATABASE, "project_suite", command,
                    "project suite passes; requirement-specific database cases are still required"))
        for command in profile.lint_commands:
            criteria.append(VerificationCriterion(VerificationKind.CODE, "lint", command,
                                                  "configured project lint passes"))
        return VerificationPlan(criteria=tuple(criteria))
