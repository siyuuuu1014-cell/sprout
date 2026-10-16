import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from Sprout.cli.commands.evolution import app
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.gateway.identity import Principal
from Sprout.storage.contracts.commit_request import commit_workflow_id
from Sprout.storage.local.sqlite.commit_status import commit_status_report
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.operational import SqliteOperationalStore
from Sprout.tests.test_commit_request_store import record
from Sprout.tests.test_git_transaction_store import fixture


async def arranged(tmp_path, *, review_secret=None):
    path, metadata, proposal, transaction, _ = await fixture(tmp_path)
    principal = Principal(user_id="owner", source="cli", authenticated=True)
    task = await metadata.get_task(transaction["task_id"])
    await metadata.save_task(replace(task, actor=principal, source="cli"))
    operational = SqliteOperationalStore(SqliteDatabase(tmp_path / "operational.db"))
    request = record()
    request.update(task_id=task.id, proposal_id=proposal.id)
    request["review"].update(task_id=task.id, proposal_id=proposal.id)
    if review_secret is not None:
        request["review"]["diffs"] = [{"diff_text": "api_key=" + review_secret}]
    request["fingerprint"] = fingerprint(request["review"])
    await operational.save_commit_request(request)
    return path, metadata, operational, request, principal


async def test_pending_status_and_cli_are_readonly(tmp_path, monkeypatch):
    path, metadata, operational, request, principal = await arranged(tmp_path)
    try:
        before = [path.read_bytes(), (tmp_path / "operational.db").read_bytes()]
        report = commit_status_report(path, request["task_id"], principal=principal)
        assert report["requests"][0]["status"] == "awaiting_confirmation"
        assert report["write_authorized"] is False and report["read_only"] is True
        monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal",
                            lambda: principal)
        result = CliRunner().invoke(app, ["commit-status", request["task_id"],
                                         "--database", str(path)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == report
        assert [path.read_bytes(), (tmp_path / "operational.db").read_bytes()] == before
    finally:
        metadata.close()
        operational.close()


async def test_scheduler_ack_is_not_reported_as_completed_execution(tmp_path):
    path, metadata, operational, request, principal = await arranged(tmp_path)
    try:
        await operational.decide_commit_request(request["id"], creator="owner",
            expected_fingerprint=request["fingerprint"], approved=True, reason="Explicit")
        dispatch = {"request_id": request["id"], "request_fingerprint": request["fingerprint"],
            "workflow_id": commit_workflow_id(request["id"], request["fingerprint"]),
            "host": "fixture", "namespace": "fixture", "task_queue": "fixture"}
        await operational.ensure_commit_dispatch(dispatch)
        await operational.mark_commit_dispatch(request["id"], "accepted", "fixture-run")
        report = commit_status_report(path, request["task_id"], principal=principal)
        assert report["requests"][0]["status"] == "accepted_waiting_execution"
        assert report["requests"][0]["result_commit"] is None
        with pytest.raises(ValueError, match="creator"):
            commit_status_report(path, request["task_id"], principal=Principal(
                user_id="foreign", source="cli", authenticated=True))
    finally:
        metadata.close()
        operational.close()


def test_missing_database_is_not_created(tmp_path):
    missing = tmp_path / "metadata.db"
    with pytest.raises(ValueError, match="Existing"):
        commit_status_report(missing, "task", principal=None)
    assert not missing.exists()


async def test_active_and_unknown_ownership_are_never_reported_as_applied(tmp_path):
    from uuid import NAMESPACE_URL, uuid5

    from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType

    path, metadata, operational, request, principal = await arranged(tmp_path)
    try:
        node = ExecutionNode(id=str(uuid5(NAMESPACE_URL, request["id"] + ":commit-apply")),
            task_id=request["task_id"], type=NodeType.APPLY,
            metadata={"commit_request_fingerprint": request["fingerprint"]})
        assert await metadata.claim_execution_node(node, "owner", "")
        assert commit_status_report(path, request["task_id"], principal=principal)[
            "requests"][0]["status"] == "executing_or_unconfirmed"
        owned = await metadata.get_execution_node(node.id)
        assert await metadata.finish_execution_node(
            replace(owned, status=NodeStatus.WAITING), "owner", "uncertain")
        assert commit_status_report(path, request["task_id"], principal=principal)[
            "requests"][0]["status"] == "needs_reconciliation"
    finally:
        metadata.close()
        operational.close()
