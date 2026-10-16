from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.client import Client

from Sprout.cli import coding_submission as module
from Sprout.orchestration.models import ExecutionGraph
from Sprout.orchestration.terminal.temporal import TemporalOrchestratorBackend
from Sprout.task.models import TaskResult, TaskStatus


async def test_submit_only_does_not_wait_for_result(monkeypatch):
    handle = SimpleNamespace(first_execution_run_id="run", result=AsyncMock())
    client = SimpleNamespace(start_workflow=AsyncMock(return_value=handle))
    monkeypatch.setattr(Client, "connect", AsyncMock(return_value=client))
    backend = TemporalOrchestratorBackend(wait_for_result=False)
    result = await backend.execute(ExecutionGraph(task_id="task"))
    assert result.status is TaskStatus.RUNNING
    assert result.metrics["submission_state"] == "accepted"
    handle.result.assert_not_awaited()
    client.start_workflow.assert_awaited_once()


def setup(monkeypatch, tmp_path, records=(), nodes=()):
    metadata = SimpleNamespace(list_task_submissions=AsyncMock(return_value=list(records)),
        list_execution_nodes=AsyncMock(return_value=list(nodes)))
    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=metadata),
        execute=AsyncMock(return_value=TaskResult(task_id="task", status=TaskStatus.RUNNING)),
        start=AsyncMock(), resume_task=AsyncMock())
    task = SimpleNamespace(status=TaskStatus.RUNNING)
    closed = []

    @asynccontextmanager
    async def scope(*args, **kwargs):
        try:
            yield runtime, task
        finally:
            closed.append(True)

    monkeypatch.setattr(module, "_regular", lambda path: path)
    monkeypatch.setattr(module, "recovery_facts", lambda *a: {"metadata": {"replay_plan": {}}})
    monkeypatch.setattr(module, "verify_plan", lambda *a: {
        "creator": {}, "repository_root": str(tmp_path / "repo")})
    monkeypatch.setattr(module, "creator_blockers", lambda *a: [])
    monkeypatch.setattr(module, "experiment_runtime", scope)
    constructor = Mock(return_value=(object(), SimpleNamespace()))
    monkeypatch.setattr(module, "assemble_coding_worker", constructor)
    return runtime, constructor, closed, tmp_path / "metadata.db"


async def test_fresh_submit_assembles_but_never_runs_worker(monkeypatch, tmp_path):
    runtime, constructor, closed, path = setup(monkeypatch, tmp_path)
    output = await module.submit_coding(path, "task", settings=None, principal=None,
                                        host="host", namespace="ns")
    assert output["worker_started"] is False
    assert runtime._orchestrator_backend._wait_for_result is False
    constructor.assert_called_once()
    runtime.execute.assert_awaited_once()
    runtime.start.assert_not_awaited()
    runtime.resume_task.assert_not_awaited()
    assert closed == [True]


@pytest.mark.parametrize("state", ["pending", "uncertain", "accepted", "resolved"])
async def test_existing_record_never_submits_or_restarts(monkeypatch, tmp_path, state):
    records = [{"id": "existing", "host": "host", "namespace": "ns", "state": state}]
    runtime, constructor, closed, path = setup(monkeypatch, tmp_path, records=records)
    output = await module.submit_coding(path, "task", settings=None, principal=None,
                                        host="host", namespace="ns")
    assert output["new_submission"] is False
    runtime.execute.assert_not_awaited()
    constructor.assert_not_called()
    assert closed == [True]


async def test_unknown_nodes_cannot_be_fresh_submission(monkeypatch, tmp_path):
    runtime, constructor, _, path = setup(monkeypatch, tmp_path, nodes=[object()])
    with pytest.raises(ValueError, match="Existing nodes"):
        await module.submit_coding(path, "task", settings=None, principal=None,
                                   host="host", namespace="ns")
    runtime.execute.assert_not_awaited()
    constructor.assert_not_called()
