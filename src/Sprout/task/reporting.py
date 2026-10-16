"""Pure report projections shared by storage readers and Gateway services."""

import json


def budget_projection(rows, configured):
    """None means unavailable evidence, never zero observed requests."""
    if rows is None:
        return {"mode": "legacy_unavailable", "configured_limit": configured,
                "consumed": None, "remaining": None, "money_hard_cap": False}
    conflict = any(row[0] != configured for row in rows)
    known_input, known_output, responded = 0, 0, 0
    for row in rows:
        if row[1] == "responded":
            usage = json.loads(row[2] or "{}")
            known_input += usage.get("prompt_tokens", 0)
            known_output += usage.get("completion_tokens", 0)
            responded += 1
    return {"mode": "configuration_conflict" if conflict else
            "persistent_limit" if configured > 0 else "no_explicit_limit",
            "configured_limit": configured, "consumed": len(rows),
            "remaining": (max(0, configured - len(rows))
                          if configured > 0 and not conflict else None),
            "responded": responded, "unknown_or_failed": len(rows) - responded,
            "known_input_tokens": known_input, "known_output_tokens": known_output,
            "token_scope": "persisted successful adapter responses only",
            "money_hard_cap": False}


def task_stage(status, nodes, submissions):
    """Interpret evidence, without deciding approvals or changing task state."""
    if status in {"completed", "cancelled", "failed", "rolled_back"}:
        return status, "Inspect final evidence; do not resubmit automatically"
    if any(node.get("budget_stopped") for node in nodes):
        return "budget_stopped", "Review allowance and retained execution evidence"
    if any(node.get("ownership") in {"uncertain", "legacy_unconfirmed"} for node in nodes):
        return "needs_reconciliation", "Review unknown effects; no automatic rerun"
    if status == "waiting_approval":
        code_review = any(node["type"] == "approval" and node["status"] == "waiting"
                          for node in nodes)
        return ("code_review" if code_review else "tool_or_process_approval",
                "Review exact proposal or pending approval; do not infer approval from this report")
    if any(node.get("ownership") in {"active", "occupied_or_unconfirmed"} for node in nodes):
        return "executing_or_unconfirmed", "Observe original workflow without resubmission"
    if submissions and submissions[-1]["state"] == "resolved":
        return ("result_available",
                "Use explicit result synchronization if task projection is pending")
    return "queued_or_waiting_resource", "Inspect original submission and worker status"
