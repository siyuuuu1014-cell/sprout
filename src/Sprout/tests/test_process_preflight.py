from types import SimpleNamespace
from unittest.mock import AsyncMock

from Sprout.execution.process_broker import ProcessBroker
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.security.approval import ApprovalManager
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.workspace.models import Workspace


async def test_preflight_uses_real_approval_without_spawn(tmp_path, monkeypatch):
    policy = SimpleNamespace(decide=lambda request: PolicyDecision(
        AccessDecision.REQUIRE_APPROVAL, "review"))
    manager = ApprovalManager(MemoryOperationalStore())
    broker = ProcessBroker(policy, approvals=manager)
    spawn = AsyncMock(side_effect=AssertionError("Preflight must not spawn"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    workspace = Workspace(id="workspace", root=tmp_path)
    kwargs = dict(task_id="task", source="cli", requested_by="owner", preflight_only=True)
    pending = await broker.run(workspace, ("docker", "run"), **kwargs)
    assert pending.needs_approval and not pending.allowed
    await manager.decide(pending.approval_id, True, decided_by="owner", channel="cli")
    approved = await broker.run(workspace, ("docker", "run"), **kwargs)
    assert approved.allowed and approved.exit_code is None
    spawn.assert_not_called()
    # Actual execution/another preflight never treats the previous response as a grant.
    policy.decide = lambda request: PolicyDecision(AccessDecision.DENY, "revoked")
    denied = await broker.run(workspace, ("docker", "run"), **kwargs)
    assert not denied.allowed
    spawn.assert_not_called()
