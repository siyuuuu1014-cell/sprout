"""Choose change mode and produce an implementation plan."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from Sprout.strategy.models import (
    ChangeMode,
    ImpactReport,
    Requirement,
    StrategyPlan,
    VerificationPlan,
)
from Sprout.strategy.project_profile import ProjectProfile


class RequirementPlanner:
    def build(
        self,
        requirement: Requirement,
        impact: ImpactReport,
        verification: VerificationPlan,
        *, profile: ProjectProfile | None = None,
    ) -> StrategyPlan:
        has_existing = bool(impact.related_code or impact.interfaces or impact.databases)
        if has_existing and not impact.tests:
            mode = ChangeMode.MIXED
        elif has_existing:
            mode = ChangeMode.MODIFY
        else:
            mode = ChangeMode.ADD
        profile = profile or ProjectProfile()
        test_files = impact.tests or ((self._default_test_path(requirement, profile),)
                                     if profile.test_suffix else ())
        steps = (
            "确认影响面证据与需求边界",
            f"按 {mode.value} 策略设计接口、数据库和代码变更",
            "先补充或更新检验标准对应的测试文件",
            "通过审批后调用 coder/apply_patch 写入代码",
            "按数据库、接口、代码三个维度执行验证",
            "验证通过后创建 ChangeProposal 并记录 Git 变更",
        )
        risks = ("影响面不足或没有匹配到现有代码时，必须人工确认新增边界",)
        if not profile.test_commands:
            risks += ("未发现目标项目测试命令，需要确认验收配置；不自动执行猜测命令",)
        return StrategyPlan(requirement, mode, impact, verification, steps, test_files, risks)

    @staticmethod
    def _default_test_path(requirement: Requirement, profile: ProjectProfile) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", requirement.text.casefold()).strip("_")[:48]
        filename = f"{profile.test_prefix}strategy_{slug or 'requirement'}{profile.test_suffix}"
        return str(PurePosixPath(profile.test_directory) / filename)
