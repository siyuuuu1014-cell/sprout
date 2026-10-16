from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.models import ProcessResult
from Sprout.gateway.identity import Principal
from Sprout.runtime.runtime import Runtime
from Sprout.task.models import Task


@pytest.mark.parametrize("source", ["cli", "cron", "automation", "mcp_client", "unknown"])
@pytest.mark.parametrize("entry", ["run", "preflight"])
async def test_task_process_uses_persisted_identity_not_interactive_defaults(source, entry):
    task = Task(workspace_id="persisted-workspace", source=source,
                actor=Principal(user_id="persisted-actor"))
    workspace = SimpleNamespace(id="persisted-workspace", root="fixture-root")
    broker = SimpleNamespace(run=AsyncMock(return_value=ProcessResult(("docker",), allowed=False)))
    runtime = SimpleNamespace(get_task=AsyncMock(return_value=task),
                              get_workspace=AsyncMock(return_value=workspace),
                              _process_broker=broker)
    if entry == "run":
        await Runtime.run_task_process(runtime, task.id, ("docker",), timeout_seconds=15)
    else:
        await Runtime.authorize_task_process(runtime, task.id, ("docker",))
        assert broker.run.call_args.kwargs["preflight_only"] is True
    assert broker.run.call_args.kwargs["source"] == source
    assert broker.run.call_args.kwargs["requested_by"] == "persisted-actor"
    assert broker.run.call_args.kwargs["actor"] == task.actor
    assert broker.run.call_args.kwargs["scope"] == task.delegation_scope
    assert broker.run.call_args.kwargs["resource_scope"] == "persisted-workspace"


async def test_missing_task_never_reaches_process_broker():
    broker = SimpleNamespace(run=AsyncMock())
    runtime = SimpleNamespace(get_task=AsyncMock(return_value=None), _process_broker=broker)
    with pytest.raises(LookupError):
        await Runtime.run_task_process(runtime, "missing", ("docker",))
    broker.run.assert_not_awaited()


@pytest.mark.parametrize("source", ["cron", "automation"])
async def test_unattended_task_reaches_real_broker_and_never_launches(
    tmp_path, monkeypatch, source
):
    from Sprout.execution.process_broker import ProcessBroker
    from Sprout.security.access import AccessDecision, PolicyDecision
    from Sprout.security.approval import ApprovalManager
    from Sprout.storage.local.memory import MemoryOperationalStore
    from Sprout.workspace.models import Workspace

    store = MemoryOperationalStore()
    manager = ApprovalManager(store)
    policy = SimpleNamespace(decide=lambda request: PolicyDecision(
        AccessDecision.REQUIRE_APPROVAL, "human review required"))
    broker = ProcessBroker(policy, approvals=manager)
    task = Task(source=source, actor=Principal(user_id="owner"))
    workspace = Workspace(id="workspace", root=tmp_path)
    runtime = SimpleNamespace(get_task=AsyncMock(return_value=task),
                              get_workspace=AsyncMock(return_value=workspace),
                              _process_broker=broker)
    spawn = AsyncMock(side_effect=AssertionError("must not spawn"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    result = await Runtime.run_task_process(runtime, task.id, ("docker", "image", "inspect"))
    assert not result.allowed
    assert "unattended" in result.error
    assert not await manager.pending()
    spawn.assert_not_awaited()
