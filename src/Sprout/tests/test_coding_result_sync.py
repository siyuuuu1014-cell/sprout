from unittest.mock import AsyncMock

import pytest

from Sprout.cli import coding_sync
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.storage.local.sqlite.submission_sync import synchronize_result
from Sprout.task.models import Task, TaskStatus


async def fixture(tmp_path, status=TaskStatus.RUNNING):
    path = tmp_path / "metadata.db"
    store = open_metadata_store(str(path))
    task = Task(status=status)
    await store.save_task(task)
    record = {"id": "submission", "task_id": task.id, "state": "accepted", "accepted": True,
              "run_id": "run", "host": "host", "namespace": "ns", "task_queue": "queue",
              "payload": {"nodes": []}}
    await store.ensure_task_submission(record)
    return path, store, task, record


@pytest.mark.parametrize("initial", [TaskStatus.RUNNING, TaskStatus.WAITING_RESOURCE])
async def test_atomic_result_and_task_projection_is_idempotent(tmp_path, initial):
    path, store, task, record = await fixture(tmp_path, initial)
    try:
        result = {"status": "waiting_approval", "node_id": "approval"}
        first = synchronize_result(path, record, result)
        second = synchronize_result(path, record, result)
        assert first["task_status"] == second["task_status"] == "waiting_approval"
        assert second["task_status_updated"] is False
        assert (await store.get_task(task.id)).status is TaskStatus.WAITING_APPROVAL
        assert (await store.list_task_submissions(task.id))[0]["state"] == "resolved"
        assert await store.list_execution_nodes(task.id) == []
    finally:
        store.close()


@pytest.mark.parametrize("initial", [TaskStatus.COMPLETED, TaskStatus.CANCELLED])
async def test_late_workflow_result_never_downgrades_terminal_task(tmp_path, initial):
    path, store, task, record = await fixture(tmp_path, initial)
    try:
        result = synchronize_result(path, record, {"status": "waiting_approval"})
        assert result["task_status"] == initial.value
        assert not result["task_status_updated"]
        assert (await store.get_task(task.id)).status is initial
    finally:
        store.close()


async def test_missing_graph_rolls_back_submission_and_task_together(tmp_path):
    path, store, task, record = await fixture(tmp_path)
    try:
        with pytest.raises(ValueError, match="graph evidence"):
            synchronize_result(path, record, {"status": "completed"})
        assert (await store.list_task_submissions(task.id))[0]["state"] == "accepted"
        assert (await store.get_task(task.id)).status is TaskStatus.RUNNING
    finally:
        store.close()


async def test_changed_run_is_rejected_without_writes(tmp_path):
    path, store, _, record = await fixture(tmp_path)
    try:
        with pytest.raises(ValueError, match="binding changed"):
            synchronize_result(path, {**record, "run_id": "other"}, {"status": "waiting_approval"})
        assert (await store.list_task_submissions(record["task_id"]))[0]["state"] == "accepted"
    finally:
        store.close()


async def test_old_submission_cannot_project_over_newer_history(tmp_path):
    path, store, task, record = await fixture(tmp_path)
    try:
        await store.ensure_task_submission({**record, "id": "newer", "run_id": "new-run"})
        output = synchronize_result(path, record, {"status": "waiting_approval"})
        assert not output["task_status_updated"]
        assert (await store.get_task(task.id)).status is TaskStatus.RUNNING
    finally:
        store.close()


async def test_result_conflict_preserves_confirmed_result(tmp_path):
    path, store, task, record = await fixture(tmp_path)
    try:
        synchronize_result(path, record, {"status": "waiting_approval"})
        with pytest.raises(ValueError, match="cannot be replaced"):
            synchronize_result(path, record, {"status": "failed"})
        assert (await store.get_task(task.id)).status is TaskStatus.WAITING_APPROVAL
        assert (await store.list_task_submissions(task.id))[0]["result"][
            "status"] == "waiting_approval"
    finally:
        store.close()


async def test_no_final_result_never_opens_writable_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(coding_sync, "_regular", lambda path: path)
    monkeypatch.setattr(coding_sync, "accepted_record", lambda *a, **k: {})
    monkeypatch.setattr(coding_sync, "observe_submission", AsyncMock(
        return_value={"observation": "running"}))
    def forbidden(*args):
        pytest.fail("Running result must not write metadata")
    monkeypatch.setattr(coding_sync, "synchronize_result", forbidden)
    assert (await coding_sync.sync_coding(tmp_path / "db", "task", principal=None))[
        "synchronized"] is False
