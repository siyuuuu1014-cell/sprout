"""Temporal gateway workflow."""

from __future__ import annotations

from typing import Any

from temporalio import workflow


@workflow.defn
class SproutGatewayWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"gateway": payload.get("transport"), "status": "submitted"}
