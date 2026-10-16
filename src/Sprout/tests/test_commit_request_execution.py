from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.commit_request_execution import execute_commit_request
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.gateway.identity import Principal
from Sprout.security.approval import ApprovalManager
from Sprout.task.models import TaskStatus
from Sprout.tests.test_isolated_git_coordinator import arranged


async def prepared(tmp_path, monkeypatch):
    metadata, operational, repo, runtime, kwargs = await arranged(tmp_path, monkeypatch)
    runtime.approvals = ApprovalManager(operational)
    admitted = await runtime.storage.metadata.get_git_proposal_snapshot(kwargs["proposal_id"])
    from Sprout.execution.isolated_git_coordinator import _admit

    args = (await _admit(runtime, kwargs["task_id"], kwargs["proposal_id"], "evaluation",
                         kwargs["contract"]))[-1]
    assert admitted is not None
    review = {"status": "awaiting_review", "task_id": kwargs["task_id"],
        "proposal_id": kwargs["proposal_id"], "proposal_status": "approved",
        "write_authorized": False, "contract": kwargs["contract"],
        "approval_arguments": args, "evaluation_node_id": "evaluation"}
    now = datetime.now(UTC)
    request = {"id": "fixture-request", "task_id": kwargs["task_id"],
        "proposal_id": kwargs["proposal_id"], "creator": "owner", "review": review,
        "fingerprint": fingerprint(review), "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat()}
    await operational.save_commit_request(request)
    await operational.decide_commit_request(request["id"], creator="owner",
        expected_fingerprint=request["fingerprint"], approved=True, reason="Explicit fixture")
    monkeypatch.setattr("Sprout.execution.commit_request_execution.current_cli_principal",
        lambda: Principal(user_id="owner", source="cli", authenticated=True))
    monkeypatch.setattr("Sprout.execution.commit_request_execution.ExperimentCommitService.review",
                        AsyncMock(return_value=review))
    return metadata, repo, runtime, request


async def test_completed_request_returns_original_result_without_rewriting(tmp_path, monkeypatch):
    metadata, repo, runtime, request = await prepared(tmp_path, monkeypatch)
    try:
        await metadata.update_task_status(request["task_id"], TaskStatus.WAITING_APPROVAL)
        result = await execute_commit_request(runtime, request["id"], request["fingerprint"])
        assert result["applied"] is True
        repeated = await execute_commit_request(runtime, request["id"], request["fingerprint"])
        assert repeated == result
        assert (repo / "app.py").read_text() == "value = 2\n"
    finally:
        metadata.close()


@pytest.mark.parametrize("status", [TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.COMPLETED,
                                    TaskStatus.FAILED, TaskStatus.WAITING_RESOURCE])
async def test_commit_continuation_never_revives_other_states(tmp_path, status):
    from Sprout.tests.test_git_transaction_store import fixture

    _, metadata, _, record, _ = await fixture(tmp_path)
    try:
        await metadata.update_task_status(record["task_id"], status)
        assert not await metadata.activate_commit_task(record["task_id"], "replay")
        assert (await metadata.get_task(record["task_id"])).status == status
    finally:
        metadata.close()


async def test_failure_retains_node_ownership_and_never_repeats(tmp_path, monkeypatch):
    metadata, _, runtime, request = await prepared(tmp_path, monkeypatch)
    commit = AsyncMock(side_effect=RuntimeError("injected"))
    monkeypatch.setattr("Sprout.execution.commit_request_execution._commit_experiment", commit)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            await execute_commit_request(runtime, request["id"], request["fingerprint"])
        result = await execute_commit_request(runtime, request["id"], request["fingerprint"])
        assert result["reason"] == "commit_execution_owned_or_unconfirmed"
        assert commit.await_count == 1
    finally:
        metadata.close()


async def test_task_settlement_recovery_never_repeats_git(tmp_path, monkeypatch):
    from Sprout.execution.commit_request_execution import _commit_experiment

    metadata, _, runtime, request = await prepared(tmp_path, monkeypatch)
    commit = AsyncMock(wraps=_commit_experiment)
    completion = AsyncMock(side_effect=[RuntimeError("DB unavailable"), True])
    monkeypatch.setattr("Sprout.execution.commit_request_execution._commit_experiment", commit)
    monkeypatch.setattr(metadata, "complete_isolated_commit_task", completion)
    try:
        first = await execute_commit_request(runtime, request["id"], request["fingerprint"])
        assert first["applied"] and first["task_completed"] is False
        assert first["completion_reason"] == "task_settlement_unconfirmed"
        second = await execute_commit_request(runtime, request["id"], request["fingerprint"])
        assert second["task_completed"] is True
        assert second["result_commit"] == first["result_commit"]
        assert commit.await_count == 1 and completion.await_count == 2
    finally:
        metadata.close()
