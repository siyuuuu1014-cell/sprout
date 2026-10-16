from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from typer.testing import CliRunner

from Sprout.cli import coding_worker as module
from Sprout.cli.commands.evolution import app
from Sprout.task.models import TaskStatus


def setup(monkeypatch, status=TaskStatus.RUNNING):
    runtime = SimpleNamespace(execute=AsyncMock(), resume_task=AsyncMock(), start=AsyncMock())
    task = SimpleNamespace(status=status)
    closed = []

    @asynccontextmanager
    async def scope(*args, **kwargs):
        try:
            yield runtime, task
        finally:
            closed.append(True)

    monkeypatch.setattr(module, "_regular", lambda path: path)
    monkeypatch.setattr(module, "_route", lambda *args: ("accepted-host", "accepted-ns", "queue"))
    monkeypatch.setattr(module, "experiment_runtime", scope)
    assemble = Mock(return_value=(object(), object()))
    monkeypatch.setattr(module, "assemble_coding_worker", assemble)
    return runtime, task, closed, assemble


async def test_standalone_worker_never_submits_or_starts_runtime(monkeypatch):
    runtime, task, closed, assemble = setup(monkeypatch)
    owner = AsyncMock()
    monkeypatch.setattr(module, "run_owned_workers", owner)
    await module.serve_coding_worker("database", "task", settings="config", principal="creator")
    assemble.assert_called_once_with(runtime, task, host="accepted-host", namespace="accepted-ns")
    owner.assert_awaited_once_with(assemble.return_value[0])
    runtime.execute.assert_not_awaited()
    runtime.resume_task.assert_not_awaited()
    runtime.start.assert_not_awaited()
    assert closed == [True]


async def test_worker_failure_unwinds_assembly(monkeypatch):
    _, _, closed, _ = setup(monkeypatch)
    monkeypatch.setattr(module, "run_owned_workers", AsyncMock(side_effect=RuntimeError("worker")))
    with pytest.raises(RuntimeError, match="worker"):
        await module.serve_coding_worker("db", "task", settings=None, principal=None)
    assert closed == [True]


async def test_completed_task_cannot_start_worker(monkeypatch):
    _, _, closed, assemble = setup(monkeypatch, TaskStatus.COMPLETED)
    with pytest.raises(ValueError, match="Active"):
        await module.serve_coding_worker("db", "task", settings=None, principal=None)
    assemble.assert_not_called()
    assert closed == [True]


def test_cli_keyboard_interrupt_does_not_cancel_task(monkeypatch):
    monkeypatch.setattr("Sprout.config.loader.load_settings", lambda path: None)
    monkeypatch.setattr(module, "serve_coding_worker", AsyncMock(side_effect=KeyboardInterrupt))
    result = CliRunner().invoke(app, ["coding-worker", "task", "--database", "metadata.db"])
    assert result.exit_code == 0
    assert "task state retained" in result.output
