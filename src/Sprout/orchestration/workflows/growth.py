"""Temporal growth workflow."""

from __future__ import annotations

from typing import Any

from temporalio import workflow

from Sprout.orchestration.activities.growth import run_growth_automation


@workflow.defn
class SproutGrowthWorkflow:
    @workflow.run
    async def run(self, workspace_id: str, trajectory_dir: str) -> dict[str, Any]:
        return await workflow.execute_activity(
            run_growth_automation,
            (workspace_id, trajectory_dir),
            start_to_close_timeout=3600,
        )
