from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.gateway import task_actions
from Sprout.gateway.identity import Principal
from Sprout.gateway.sdk_gateway import SDKGateway
from Sprout.task.models import Task


@pytest.mark.parametrize("observation", ["running", "unavailable", "wait_expired"])
async def test_nonfinal_observation_never_writes(monkeypatch, observation):
    principal = Principal(user_id="owner", authenticated=True, source="sdk-host")
    task = Task(actor=principal)
    runtime = SimpleNamespace(
        get_task=AsyncMock(return_value=task),
        get_task_submissions=AsyncMock(return_value=[{
            "id": "submission", "task_id": task.id, "state": "accepted"}]),
        synchronize_task_result=AsyncMock(),
    )
    monkeypatch.setattr(task_actions, "observe_submission",
                        AsyncMock(return_value={"observation": observation}))
    report = await SDKGateway(runtime, principal=principal).sync_task(task.id)
    assert report["synchronized"] is False
    runtime.synchronize_task_result.assert_not_called()


async def test_foreign_owner_cannot_observe_or_sync(monkeypatch):
    runtime = SimpleNamespace(get_task=AsyncMock(return_value=Task(actor=Principal(
        user_id="owner", authenticated=True, source="web"))))
    observer = AsyncMock()
    monkeypatch.setattr(task_actions, "observe_submission", observer)
    with pytest.raises(PermissionError):
        await SDKGateway(runtime, principal=Principal(
            user_id="other", authenticated=True, source="web")).sync_task("task")
    observer.assert_not_called()
