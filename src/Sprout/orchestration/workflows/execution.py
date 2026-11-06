"""Deterministic execution graph scheduling."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow

from Sprout.orchestration.activities.node import run_sprout_node

_VERIFY_LOOP_NODE_TYPES = frozenset({"agent", "evaluation"})


@workflow.defn
class SproutExecutionWorkflow:
    @workflow.run
    async def run(self, graph: dict[str, Any]) -> dict[str, Any]:
        # Scheduling changes must retain the old command sequence when replaying
        # histories from repository baseline 7b377ac. Never remove this marker
        # until those histories have aged out or a migration is verified.
        resume_skips = workflow.patched("sprout-resume-skips-v1")
        results: list[dict[str, Any]] = []
        nodes = list(graph.get("nodes", []))
        index = 0
        while index < len(nodes):
            node = nodes[index]
            if node.get("status") == "completed" or (
                resume_skips and node.get("status") == "skipped"
            ):
                results.append(
                    {
                        "node_id": node.get("id"),
                        "status": "completed",
                        "skipped": True,
                    }
                )
                output = node.get("metadata", {}).get("output", {})
                if (
                    resume_skips
                    and node.get("type") == "evaluation"
                    and output.get("status") == "passed"
                ):
                    index = _skip_verify_loop(nodes, index + 1, results)
                    continue
                index += 1
                continue
            try:
                result = await workflow.execute_activity(
                    run_sprout_node,
                    node,
                    start_to_close_timeout=timedelta(
                        seconds=float(node.get("budget", {}).get("timeout_seconds", 0)) or 3600
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - workflow must return a structured result
                error = f"{type(exc).__name__}: {exc}"
                results.append(
                    {
                        "node_id": node.get("id"),
                        "status": "failed",
                        "error": error,
                    }
                )
                return {"status": "failed", "nodes": results, "error": error}
            results.append(result)
            if (
                isinstance(result, dict)
                and result.get("status") == "cancelled"
                and workflow.patched("sprout-cancel-stop-v1")
            ):
                return {"status": "cancelled", "nodes": results}
            output = result.get("output") if isinstance(result, dict) else None
            if isinstance(output, dict) and output.get("waiting"):
                return {
                    "status": "waiting_resource"
                    if output.get("waiting_kind") == "resource"
                    else "waiting_approval",
                    "node_id": result.get("node_id"),
                    "nodes": results,
                }
            if (
                node.get("type") == "evaluation"
                and isinstance(output, dict)
                and output.get("status") == "passed"
            ):
                index = _skip_verify_loop(nodes, index + 1, results)
                continue
            index += 1
        return {"status": "completed", "nodes": results}


def _skip_verify_loop(
    nodes: list[dict[str, Any]], start: int, results: list[dict[str, Any]]
) -> int:
    """Mark the remaining edit/test rounds skipped after a green evaluation."""
    index = start
    while index < len(nodes) and nodes[index].get("type") in _VERIFY_LOOP_NODE_TYPES:
        results.append(
            {
                "node_id": nodes[index].get("id"),
                "status": "skipped",
                "skipped": True,
            }
        )
        index += 1
    return index
