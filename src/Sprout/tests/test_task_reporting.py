import pytest

from Sprout.task.reporting import budget_projection, task_stage


@pytest.mark.parametrize("status,nodes,records,expected", [
    ("completed", [{"budget_stopped": True}], [], "completed"),
    ("running", [{"budget_stopped": True, "ownership": "uncertain"}], [], "budget_stopped"),
    ("running", [{"ownership": "legacy_unconfirmed"}], [], "needs_reconciliation"),
    ("running", [{"ownership": "uncertain"}], [], "needs_reconciliation"),
    ("waiting_approval", [{"type": "approval", "status": "waiting"}], [], "code_review"),
    ("waiting_approval", [], [], "tool_or_process_approval"),
    ("running", [{"ownership": "occupied_or_unconfirmed"}], [], "executing_or_unconfirmed"),
    ("running", [], [{"state": "resolved"}], "result_available"),
    ("created", [], [], "queued_or_waiting_resource"),
])
def test_shared_stage(status, nodes, records, expected):
    assert task_stage(status, nodes, records)[0] == expected


def test_budget_missing_unlimited_and_conflict_are_distinct():
    assert budget_projection(None, 3)["consumed"] is None
    assert budget_projection([], 0)["mode"] == "no_explicit_limit"
    report = budget_projection([(2, "responded", '{"prompt_tokens":4}')], 3)
    assert report["mode"] == "configuration_conflict"
    assert report["remaining"] is None
    assert report["known_input_tokens"] == 4
