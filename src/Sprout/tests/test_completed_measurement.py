import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.cli import completed_measurement as module


def setup(monkeypatch, tmp_path):
    completion = {"request_id": "request", "result_commit": "commit"}
    task = SimpleNamespace(status="completed", metadata={"isolated_commit_completion": completion,
        "replay_plan": {"fingerprint": "plan-fp"}, "acceptance_spec": {},
        "regression_fingerprint": "regression"})
    plan = {"source_task_id": "source", "revision": "base", "candidate": None,
        "repository_root": str(tmp_path / "repo"), "original_root": str(tmp_path / "source"),
        "verification_image": "image", "test_commands": ["pytest"],
        "regression_commands": ["pytest"], "oracle_paths": ["tests"], "oracle_snapshot": {}}
    metadata = SimpleNamespace(get_task=AsyncMock(return_value=task))
    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=metadata),
        _settings=SimpleNamespace(security=SimpleNamespace(classify=SimpleNamespace(rules={}))),
        pending_approvals=AsyncMock(return_value=[]))

    @asynccontextmanager
    async def assembled(*args, **kwargs):
        yield runtime, task

    monkeypatch.setattr(module, "_regular", lambda path: path)
    monkeypatch.setattr(module, "_route", Mock())
    monkeypatch.setattr(module, "recovery_facts", lambda *a: {
        "status": task.status, "metadata": task.metadata})
    monkeypatch.setattr(module, "commit_status_report", lambda *a, **k: {
        "requests": [{"task_completed": True, "status": "applied"}]})
    monkeypatch.setattr(module, "experiment_runtime", assembled)
    monkeypatch.setattr(module, "build_coding_session_factory", Mock())
    monkeypatch.setattr(module, "verify_plan", lambda value: plan)
    check = Mock(return_value=tmp_path / "repo")
    monkeypatch.setattr(module, "_check_repository", check)
    monkeypatch.setattr("Sprout.evolution.replay_oracle.integrity", lambda *a: True)
    docker = AsyncMock(return_value=[{"kind": "test", "allowed": True, "exit_code": 0}])
    functions = AsyncMock(return_value=[{"kind": "function", "passed": True}])
    monkeypatch.setattr("Sprout.evolution.replay_docker.measure_docker", docker)
    monkeypatch.setattr("Sprout.evolution.replay_functions.measure_functions", functions)
    return task, docker, functions, check, runtime


async def test_new_report_preserves_wait_report_and_repeated_call_is_cached(monkeypatch, tmp_path):
    _, docker, functions, _, _ = setup(monkeypatch, tmp_path)
    original = tmp_path / "independent-measurement.json"
    original.write_text('{"measurement_status":"not_measured"}', encoding="utf-8")
    before = original.read_bytes()
    result = await module.measure_completed(tmp_path / "metadata.db", "task",
                                           settings=None, principal=None)
    assert result["measurement_status"] == "observed" and not result["validation_committed"]
    report = json.loads((tmp_path / module.COMPLETED_MEASUREMENT).read_text(encoding="utf-8"))
    assert report["result_commit"] == "commit" and report["task_status"] == "completed"
    assert original.read_bytes() == before
    final_before = (tmp_path / module.COMPLETED_MEASUREMENT).read_bytes()
    assert (await module.measure_completed(tmp_path / "metadata.db", "task",
                                          settings=None, principal=None))["cached"] is True
    assert (tmp_path / module.COMPLETED_MEASUREMENT).read_bytes() == final_before
    docker.assert_awaited_once()
    assert functions.await_count == 2


@pytest.mark.parametrize("status", ["running", "waiting_approval", "cancelled"])
async def test_incomplete_tasks_do_not_measure(monkeypatch, tmp_path, status):
    task, docker, _, _, _ = setup(monkeypatch, tmp_path)
    task.status = status
    with pytest.raises(ValueError, match="completed isolated commit"):
        await module.measure_completed(tmp_path / "metadata.db", "task",
                                       settings=None, principal=None)
    docker.assert_not_awaited()
    assert not (tmp_path / module.COMPLETED_MEASUREMENT).exists()


async def test_waiting_does_not_seal_a_final_report(monkeypatch, tmp_path):
    _, docker, functions, _, _ = setup(monkeypatch, tmp_path)
    docker.return_value = [{"waiting": True, "reason": "unknown_execution"}]
    result = await module.measure_completed(tmp_path / "metadata.db", "task",
                                           settings=None, principal=None)
    assert result["measurement_status"] == "waiting_resource"
    assert result["measurement_path"] is None and result["blockers"] == ["unknown_execution"]
    functions.assert_not_awaited()
    assert not (tmp_path / module.COMPLETED_MEASUREMENT).exists()


async def test_changed_commit_during_measurement_does_not_write_report(monkeypatch, tmp_path):
    _, _, _, check, _ = setup(monkeypatch, tmp_path)
    check.side_effect = [tmp_path / "repo", ValueError("repository changed")]
    with pytest.raises(ValueError, match="repository changed"):
        await module.measure_completed(tmp_path / "metadata.db", "task",
                                       settings=None, principal=None)
    assert not (tmp_path / module.COMPLETED_MEASUREMENT).exists()


async def test_mismatched_existing_report_is_not_overwritten_or_rerun(monkeypatch, tmp_path):
    _, docker, _, _, _ = setup(monkeypatch, tmp_path)
    report = tmp_path / module.COMPLETED_MEASUREMENT
    report.write_text('{"task_id":"other-task"}', encoding="utf-8")
    before = report.read_bytes()
    with pytest.raises(ValueError, match="binding mismatch"):
        await module.measure_completed(tmp_path / "metadata.db", "task",
                                       settings=None, principal=None)
    docker.assert_not_awaited()
    assert report.read_bytes() == before


@pytest.mark.parametrize("wrong_scope", [False, True])
async def test_only_explicit_scoped_process_request_can_be_approved(
    monkeypatch, tmp_path, wrong_scope,
):
    from Sprout.security.approval import ApprovalRecord, ApprovalStatus

    task, docker, _, _, runtime = setup(monkeypatch, tmp_path)
    task.workspace_id = "workspace"
    principal = SimpleNamespace(user_id="operator")
    grant = ApprovalRecord(tool="process_run", arguments_fingerprint="exact",
        task_id="task", source="cli", requested_by="operator",
        resource_scope="other" if wrong_scope else "workspace")

    async def decide(*args, **kwargs):
        grant.status = ApprovalStatus.APPROVED
        return grant

    runtime.approvals = SimpleNamespace(store=SimpleNamespace(
        get_approval=AsyncMock(return_value=grant)), decide=AsyncMock(side_effect=decide))
    if wrong_scope:
        with pytest.raises(ValueError, match="current-task pending process"):
            await module.measure_completed(tmp_path / "metadata.db", "task", settings=None,
                                            principal=principal, approval_id=grant.id)
        docker.assert_not_awaited()
        runtime.approvals.decide.assert_not_awaited()
    else:
        await module.measure_completed(tmp_path / "metadata.db", "task", settings=None,
                                        principal=principal, approval_id=grant.id)
        runtime.approvals.decide.assert_awaited_once()
        docker.assert_awaited_once()


def test_cli_routes_measurement_without_executing_task(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from Sprout.cli.commands.evolution import app

    settings = object()
    monkeypatch.setattr("Sprout.config.loader.load_settings", lambda path=None: settings)
    service = AsyncMock(return_value={"measurement_status": "observed",
                                     "validation_committed": False})
    monkeypatch.setattr(module, "measure_completed", service)
    result = CliRunner().invoke(app, ["replay-measure", "task", "--database",
        str(tmp_path / "metadata.db"), "--approve", "exact-process"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["validation_committed"] is False
    assert service.await_args.kwargs["settings"] is settings
    assert service.await_args.kwargs["approval_id"] == "exact-process"
