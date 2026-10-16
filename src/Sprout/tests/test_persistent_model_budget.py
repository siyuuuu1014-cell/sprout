import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.llm.base import ModelBudgetExhausted
from Sprout.llm.client import invoke_model
from Sprout.llm.messages import LLMResponse
from Sprout.llm.orchestrator import ModelOrchestrator
from Sprout.llm.task_budget import task_model_budget
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskBudget


async def fixture(tmp_path, limit=2):
    path = tmp_path / "metadata.db"
    store = open_metadata_store(str(path))
    task = Task(budget=TaskBudget(max_model_calls=limit))
    await store.save_task(task)
    return path, store, task


def provider():
    return SimpleNamespace(name="local", model="local", chat=AsyncMock(return_value=LLMResponse()))


async def test_budget_survives_node_and_database_reopen(tmp_path):
    path, store, task = await fixture(tmp_path)
    model = provider()
    with task_model_budget(store, task.id, "plan", 2):
        await invoke_model(model, [])
    store.close()
    store = open_metadata_store(str(path))
    try:
        with task_model_budget(store, task.id, "agent", 2):
            await invoke_model(model, [])
            with pytest.raises(ModelBudgetExhausted):
                await invoke_model(model, [])
        assert model.chat.await_count == 2
        assert (await store.model_budget_summary(task.id))["consumed"] == 2
    finally:
        store.close()


async def test_concurrent_requests_cannot_exceed_task_limit(tmp_path):
    _, store, task = await fixture(tmp_path, 3)
    model = provider()
    try:
        with task_model_budget(store, task.id, "node", 3):
            results = await asyncio.gather(*(invoke_model(model, []) for _ in range(9)),
                                           return_exceptions=True)
        assert model.chat.await_count == 3
        assert sum(isinstance(result, ModelBudgetExhausted) for result in results) == 6
    finally:
        store.close()


async def test_failed_attempt_counts_and_dispatcher_cannot_retry_past_limit(tmp_path):
    _, store, task = await fixture(tmp_path, 1)
    primary, backup = provider(), provider()
    primary.chat.side_effect = TimeoutError("local simulated timeout")
    try:
        with task_model_budget(store, task.id, "node", 1):
            with pytest.raises(ModelBudgetExhausted):
                await invoke_model(ModelOrchestrator([primary, backup]), [])
        primary.chat.assert_awaited_once()
        backup.chat.assert_not_awaited()
        summary = await store.model_budget_summary(task.id)
        assert summary["consumed"] == summary["unknown_or_failed"] == 1
    finally:
        store.close()


async def test_dispatcher_is_not_counted_twice(tmp_path, monkeypatch):
    _, store, task = await fixture(tmp_path, 1)
    recorder = SimpleNamespace(record=Mock())
    monkeypatch.setattr("Sprout.llm.client.get_usage_recorder", lambda: recorder)
    try:
        with task_model_budget(store, task.id, "node", 1):
            await invoke_model(ModelOrchestrator([provider()]), [])
        assert (await store.model_budget_summary(task.id))["consumed"] == 1
        recorder.record.assert_called_once()
    finally:
        store.close()


async def test_missing_budget_authority_never_sends_request():
    model = provider()
    with task_model_budget(SimpleNamespace(), "task", "node", 1):
        with pytest.raises(ModelBudgetExhausted, match="unavailable"):
            await invoke_model(model, [])
    model.chat.assert_not_awaited()


async def test_persisted_limit_mismatch_cannot_silently_reset(tmp_path):
    _, store, task = await fixture(tmp_path, 2)
    model = provider()
    try:
        with task_model_budget(store, task.id, "node", 3):
            with pytest.raises(ModelBudgetExhausted):
                await invoke_model(model, [])
        model.chat.assert_not_awaited()
    finally:
        store.close()


async def test_independent_processes_share_one_allowance(tmp_path):
    path, store, task = await fixture(tmp_path, 4)
    store.close()

    async def child():
        process = await asyncio.create_subprocess_exec(sys.executable, "-m",
            "Sprout.tests.model_budget_process_fixture", str(path), task.id, "--limit", "4",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=20)
            assert process.returncode == 0, err
            return json.loads(out)["granted"]
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    assert sum(await asyncio.gather(child(), child())) == 4
    store = open_metadata_store(str(path))
    try:
        assert (await store.model_budget_summary(task.id))["consumed"] == 4
        assert await store.reserve_model_request(task.id, "restart", 4) is None
    finally:
        store.close()
