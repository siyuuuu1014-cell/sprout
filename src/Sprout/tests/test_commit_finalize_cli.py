import asyncio
import json
from uuid import NAMESPACE_URL, uuid5

import pytest
from typer.testing import CliRunner

from Sprout.cli.commands.evolution import app
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.gateway.identity import Principal
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.operational import SqliteOperationalStore
from Sprout.task.models import TaskStatus
from Sprout.tests.test_commit_request_store import record
from Sprout.tests.test_commit_status import arranged
from Sprout.tests.test_commit_task_completion import fixture


async def test_finalize_requires_applied_result(tmp_path, monkeypatch):
    path, metadata, operational, request, principal = await arranged(tmp_path)
    monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal", lambda: principal)
    try:
        result = await asyncio.to_thread(CliRunner().invoke, app,
            ["commit-finalize", request["task_id"], request["id"], "--database", str(path)])
        assert result.exit_code == 1 and "applied result required" in result.output
        assert await operational.get_commit_dispatch(request["id"]) is None
    finally:
        metadata.close()
        operational.close()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_finalize_recovers_database_only_without_runtime(tmp_path, monkeypatch, cancelled):
    metadata = await fixture(tmp_path)
    operational = SqliteOperationalStore(SqliteDatabase(tmp_path / "operational.db"))
    request = record()
    request["review"].update(evaluation_node_id="task:evaluation",
                             approval_arguments={"fixture": "exact"})
    request["fingerprint"] = fingerprint(request["review"])
    await operational.save_commit_request(request)
    await operational.decide_commit_request(request["id"], creator="owner",
        expected_fingerprint=request["fingerprint"], approved=True, reason="Explicit fixture")
    node_id = str(uuid5(NAMESPACE_URL, "request:commit-apply"))
    node = await metadata.get_execution_node(node_id)
    await metadata._db.execute("UPDATE execution_nodes SET metadata_json=? WHERE id=?",
        (json.dumps({**node.metadata, "commit_request_fingerprint": request["fingerprint"]}),
         node_id))
    tx = await metadata.get_git_transaction("transaction")
    tx.pop("state")
    tx.pop("result")
    tx["approval_arguments"] = request["review"]["approval_arguments"]
    await metadata._db.execute("UPDATE isolated_git_transactions SET record_json=? WHERE id=?",
                               (json.dumps(tx), "transaction"))
    principal = Principal(user_id="owner", authenticated=True, source="cli")
    monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal", lambda: principal)

    def no_runtime(*args, **kwargs):
        pytest.fail("Database finalization must not construct Runtime")

    monkeypatch.setattr("Sprout.runtime.factory.create_runtime", no_runtime)
    if cancelled:
        await metadata.update_task_status("task", TaskStatus.CANCELLED)
    try:
        args = ["commit-finalize", "task", "request", "--database", str(tmp_path / "metadata.db")]
        result = await asyncio.to_thread(CliRunner().invoke, app, args)
        assert result.exit_code == 0, (result.output, result.exception)
        data = json.loads(result.output)
        assert data["git_executed"] is False and data["task_completed"] is not cancelled
        repeated = await asyncio.to_thread(CliRunner().invoke, app, args)
        assert repeated.exit_code == 0 and json.loads(repeated.output) == data
        assert (await metadata.get_task("task")).status == (
            TaskStatus.CANCELLED if cancelled else TaskStatus.COMPLETED)
    finally:
        metadata.close()
        operational.close()
