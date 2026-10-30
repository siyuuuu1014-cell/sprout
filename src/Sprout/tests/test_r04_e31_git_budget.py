"""Only new explicitly scoped Local Apply tasks get an approval review budget."""

from Sprout.orchestration.compiler import ExecutionGraphBuilder
from Sprout.task.models import DelegationScope, Task
from Sprout.workspace.models import ReadPlan, WorkspaceManifest


def test_local_approval_uses_existing_configured_verification_budget():
    for actions, expected in [
        (frozenset({"file.apply", "git.diff"}), 180),
        (frozenset({"file.apply"}), 0),
        (frozenset(), 0),
    ]:
        task = Task(instruction="change", delegation_scope=DelegationScope(allowed_actions=actions))
        graph = ExecutionGraphBuilder().build(
            task,
            ReadPlan(task_id=task.id),
            WorkspaceManifest(workspace_id="fixture"),
            evaluation_timeout_seconds=180,
        )
        assert (
            next(n for n in graph.nodes if n.type.value == "approval").budget.timeout_seconds
            == expected
        )


async def test_stop_retains_unconfirmed_local_git_custody():
    import asyncio

    from Sprout.tests.conftest import build_runtime

    runtime, _ = build_runtime()
    state = {
        "git_active": {
            "owner": asyncio.current_task(),
            "processes": {"unconfirmed": {"spawn": None, "cleanup": None}},
        }
    }
    runtime._local_git_reviews["unit-task"] = state
    await runtime.stop()
    assert runtime._verification_stopping
    assert runtime._verification_shutdown["state"] == "UNKNOWN"
    assert runtime._verification_shutdown["storage_retained"]
    assert runtime._local_git_reviews["unit-task"] is state
    # Unit data only: no OS child or Receipt was fabricated.
    runtime._local_git_reviews.clear()
