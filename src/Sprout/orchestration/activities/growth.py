"""Runtime-backed growth activity."""

from __future__ import annotations

from typing import Any

from temporalio import activity

from Sprout.orchestration.activities import node


@activity.defn
async def run_growth_automation(workspace_id: str, trajectory_dir: str) -> dict[str, Any]:
    return await _run_growth(node._activity_runtime, workspace_id, trajectory_dir)


def bind_growth_activity(runtime):
    """Bind growth execution to the Runtime owned by this Worker."""
    @activity.defn(name="run_growth_automation")
    async def execute_growth(workspace_id: str, trajectory_dir: str) -> dict[str, Any]:
        return await _run_growth(runtime, workspace_id, trajectory_dir)

    return execute_growth


async def _run_growth(runtime, workspace_id: str, trajectory_dir: str) -> dict[str, Any]:
    if runtime is None:
        raise RuntimeError("Sprout worker has no runtime configured")
    from Sprout.evolution.automation import GrowthAutomation

    metadata = runtime.storage.metadata
    if metadata is None:
        raise RuntimeError("MetadataStore is not configured")
    automation = GrowthAutomation(metadata, workspace_id, trajectory_dir, runtime=runtime)
    return await automation.run_once()
