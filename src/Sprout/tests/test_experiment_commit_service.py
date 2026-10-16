from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.git_identity import GitIdentityRequired
from Sprout.gateway.identity import Principal
from Sprout.runtime.experiment_commits import ExperimentCommitService
from Sprout.storage.local.memory.operational import MemoryOperationalStore


def arranged(monkeypatch):
    task = SimpleNamespace(metadata={"replay_plan": {"fixture": True}})
    plan = {"target": "experiment/repo", "base_commit": "a" * 40}
    context = AsyncMock(return_value=(task, {"original_root": "original"}, None, None, plan))
    resolve = AsyncMock(return_value={"fixture": "contract"})
    admit = AsyncMock(return_value=(task, {}, None, plan,
        {"status": "pending", "diffs_json": "[]", "files_changed_json": '["app.py"]'},
        {"fixture": "bound-arguments"}))
    monkeypatch.setattr("Sprout.runtime.experiment_commits._review_context", context)
    monkeypatch.setattr("Sprout.runtime.experiment_commits.resolve_commit_contract", resolve)
    monkeypatch.setattr("Sprout.runtime.experiment_commits._admit", admit)
    metadata = SimpleNamespace(list_execution_nodes=AsyncMock(return_value=[]))
    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=metadata))
    return ExperimentCommitService(runtime), context, resolve, admit


async def test_review_returns_bound_display_without_write_methods(monkeypatch):
    service, context, _, admit = arranged(monkeypatch)
    result = await service.review("task", "proposal", message="Explicit", evaluation_node_id="eval")
    assert result["write_authorized"] is False
    assert result["configuration_changed"] is False
    assert result["validation_committed"] is False
    assert result["files_changed"] == ["app.py"]
    assert context.await_args.kwargs["review_only"] is True
    assert admit.await_args.kwargs["review_only"] is True


async def test_missing_identity_is_actionable_and_no_admission_attempt(monkeypatch):
    service, _, resolve, admit = arranged(monkeypatch)
    resolve.side_effect = GitIdentityRequired(["user.email"])
    result = await service.review("task", "proposal", message="Explicit")
    assert result["missing_fields"] == ["user.email"]
    assert result["automatic_retry"] is False
    admit.assert_not_awaited()


async def test_no_unique_evaluation_requires_selection(monkeypatch):
    service, _, _, admit = arranged(monkeypatch)
    result = await service.review("task", "proposal", message="Explicit")
    assert result["status"] == "waiting_selection"
    admit.assert_not_awaited()


async def test_plan_drift_is_not_displayed_as_valid_review(monkeypatch):
    service, _, _, admit = arranged(monkeypatch)
    admitted = list(admit.return_value)
    admitted[3] = {"changed": True}
    admit.return_value = tuple(admitted)
    with pytest.raises(ValueError, match="changed"):
        await service.review("task", "proposal", message="Explicit", evaluation_node_id="eval")


async def test_service_persists_creator_decision_without_execution(monkeypatch):
    service, _, _, _ = arranged(monkeypatch)
    store = MemoryOperationalStore()
    service._runtime.storage.operational = store
    service.review = AsyncMock(return_value={"status": "awaiting_review", "task_id": "task",
        "proposal_id": "proposal", "proposal_status": "approved", "write_authorized": False})
    monkeypatch.setattr("Sprout.runtime.experiment_commits.current_cli_principal",
        lambda: Principal(user_id="owner", source="cli", authenticated=True))
    request = await service.request("task", "proposal", message="Explicit")
    assert (await service.request("task", "proposal", message="Explicit"))["id"] == request["id"]
    assert await service.decide(request["id"], expected_fingerprint=request["fingerprint"],
                                approved=True, reason="Explicit")
    assert (await store.get_commit_request(request["id"]))["dispatch_state"] == "waiting_schedule"
    assert await store.list_approvals() == []
    monkeypatch.setattr("Sprout.runtime.experiment_commits.current_cli_principal",
        lambda: Principal(user_id="other", source="cli", authenticated=True))
    with pytest.raises(ValueError, match="creator"):
        await service.decide(request["id"], expected_fingerprint=request["fingerprint"],
                             approved=True)
