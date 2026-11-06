"""Temporal task workflow."""

from __future__ import annotations

from typing import Any

from temporalio import workflow


@workflow.defn
class SproutTaskWorkflow:
    @workflow.run
    async def run(self, task: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": task.get("id", ""),
            "status": "queued",
        }
