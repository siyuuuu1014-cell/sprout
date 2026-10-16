import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from Sprout.cli.commands.evolution import app
from Sprout.cli.commit_submission import submit_cli_commit
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.orchestration.isolated_worker import COMMIT_ROUTING_PROFILE, experiment_task_queue
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.operational import SqliteOperationalStore
from Sprout.task.models import TaskStatus
from Sprout.tests.test_coding_assembly import fixture
from Sprout.tests.test_commit_request_store import record


async def arranged(tmp_path, monkeypatch, *, routing_profile=None):
    metadata, _, task, _, _ = await fixture(tmp_path, monkeypatch)
    directory = Path(task.metadata["replay_plan"]["payload"]["repository_root"]).parent
    operational = SqliteOperationalStore(SqliteDatabase(directory / "operational.db"))
    request = record()
    request.update(task_id=task.id, creator=task.actor.user_id)
    request["review"]["task_id"] = task.id
    if routing_profile is not None:
        request["review"]["routing_profile"] = routing_profile
    request["fingerprint"] = fingerprint(request["review"])
    await operational.save_commit_request(request)
    await operational.decide_commit_request(request["id"], creator=task.actor.user_id,
        expected_fingerprint=request["fingerprint"], approved=True, reason="Explicit fixture")
    route = {"id": "coding-submission", "task_id": task.id, "accepted": True,
        "run_id": "fixture-coding-run", "state": "resolved", "host": "127.0.0.1:17233",
        "namespace": "fixture", "task_queue": experiment_task_queue(task)}
    await metadata.ensure_task_submission(route)
    return metadata, operational, task, request, route, directory / "metadata.db"


@pytest.mark.parametrize("profile", [None, COMMIT_ROUTING_PROFILE])
async def test_cli_submit_preserves_route_and_caches(tmp_path, monkeypatch, profile):
    metadata, operational, task, request, route, path = await arranged(
        tmp_path, monkeypatch, routing_profile=profile)
    start = AsyncMock(return_value=SimpleNamespace(first_execution_run_id="fixture-run"))
    connect = AsyncMock(return_value=SimpleNamespace(start_workflow=start))
    monkeypatch.setattr("temporalio.client.Client.connect", connect)
    monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal", lambda: task.actor)
    try:
        result = await asyncio.to_thread(CliRunner().invoke, app,
            ["commit-submit", task.id, request["id"], "--database", str(path)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["dispatch_state"] == "accepted"
        assert json.loads(result.output)["local_execution"] is False
        assert connect.await_args.args == (route["host"],)
        assert connect.await_args.kwargs["namespace"] == route["namespace"]
        expected = route["task_queue"] + ("-commit-v1" if profile else "")
        assert start.await_args.kwargs["task_queue"] == expected
        await submit_cli_commit(path, task.id, request["id"], principal=task.actor)
        assert start.await_count == 1
        assert await operational.list_approvals() == []
    finally:
        metadata.close()
        operational.close()


@pytest.mark.parametrize("change", ["cancelled", "route", "fingerprint"])
async def test_ineligible_request_never_connects_or_dispatches(tmp_path, monkeypatch, change):
    metadata, operational, task, request, route, path = await arranged(tmp_path, monkeypatch)
    connect = AsyncMock()
    monkeypatch.setattr("temporalio.client.Client.connect", connect)
    try:
        if change == "cancelled":
            await metadata.update_task_status(task.id, TaskStatus.CANCELLED)
        elif change == "route":
            await metadata.ensure_task_submission({**route, "id": "conflicting-submission",
                                                   "namespace": "different"})
        with pytest.raises(ValueError):
            await submit_cli_commit(path, task.id, request["id"], principal=task.actor,
                expected_fingerprint="changed" if change == "fingerprint" else None)
        connect.assert_not_awaited()
        assert await operational.get_commit_dispatch(request["id"]) is None
    finally:
        metadata.close()
        operational.close()
