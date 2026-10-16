import json
from dataclasses import replace

import pytest

from Sprout.gateway.local_identity import current_cli_principal
from Sprout.llm.base import ModelBudgetExhausted
from Sprout.orchestration.execution_guard import NodeExecutionGuard
from Sprout.orchestration.models import ExecutionNode
from Sprout.storage.local.sqlite.coding_report import coding_report
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.storage.local.sqlite.operational import open_operational_store
from Sprout.task.models import Task, TaskBudget, TaskStatus


async def fixture(tmp_path):
    path = tmp_path / "metadata.db"
    store = open_metadata_store(str(path))
    operations = open_operational_store(str(tmp_path / "operational.db"))
    task = Task(actor=current_cli_principal(), source="cli", status=TaskStatus.RUNNING,
                budget=TaskBudget(max_model_calls=3))
    await store.save_task(task)
    return path, store, operations, task


async def test_persisted_budget_is_reported_without_spending_or_execution(tmp_path):
    path, store, operations, task = await fixture(tmp_path)
    try:
        request = await store.reserve_model_request(task.id, "plan", 3)
        await store.finish_model_request(request, "responded", {"prompt_tokens": 5,
                                                                "completion_tokens": 2})
        await store.reserve_model_request(task.id, "agent", 3)
        before = await store.model_budget_summary(task.id)
        report = coding_report(path, task.id, principal=task.actor)
        assert report["budget"]["consumed"] == 2
        assert report["budget"]["remaining"] == 1
        assert report["budget"]["unknown_or_failed"] == 1
        assert report["budget"]["known_input_tokens"] == 5
        assert await store.model_budget_report(task.id, 3) == report["budget"]
        assert report["read_only"] and not report["execution_authority"]
        assert await store.model_budget_summary(task.id) == before
        assert await store.list_execution_nodes(task.id) == []
    finally:
        store.close()
        operations.close()


async def test_legacy_ledger_missing_is_not_zero_usage(tmp_path):
    path, store, operations, task = await fixture(tmp_path)
    try:
        await store._db.execute("DROP TABLE task_model_requests")
        report = coding_report(path, task.id, principal=task.actor)
        assert report["budget"]["mode"] == "legacy_unavailable"
        assert report["budget"]["consumed"] is None
        assert await store.model_budget_report(task.id, 3) == report["budget"]
    finally:
        store.close()
        operations.close()


async def test_unknown_node_is_manual_review_without_raw_model_output(tmp_path):
    path, store, operations, task = await fixture(tmp_path)
    node = ExecutionNode(task_id=task.id)
    try:
        async def execute(running):
            raise RuntimeError("private model prompt must not appear")
        await NodeExecutionGuard(store).execute(node, execute)
        before = await store.get_execution_node(node.id)
        report = coding_report(path, task.id, principal=task.actor)
        assert report["stage"] == "needs_reconciliation"
        assert "private model prompt" not in json.dumps(report)
        assert await store.get_execution_node(node.id) == before
    finally:
        store.close()
        operations.close()


async def test_foreign_principal_cannot_read_report(tmp_path):
    path, store, operations, task = await fixture(tmp_path)
    try:
        with pytest.raises(ValueError, match="creator"):
            coding_report(path, task.id, principal=replace(task.actor, user_id="other"))
    finally:
        store.close()
        operations.close()


async def test_budget_stop_is_explained_without_releasing_unknown_node(tmp_path):
    path, store, operations, task = await fixture(tmp_path)
    node = ExecutionNode(task_id=task.id)
    try:
        async def execute(running):
            raise ModelBudgetExhausted("limit exhausted")
        await NodeExecutionGuard(store).execute(node, execute)
        report = coding_report(path, task.id, principal=task.actor)
        assert report["stage"] == "budget_stopped"
        assert report["nodes"][0]["ownership"] == "uncertain"
        assert report["automatic_retry"] is False
    finally:
        store.close()
        operations.close()
