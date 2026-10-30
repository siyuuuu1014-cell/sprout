"""Bounded closure checks; no model, Docker or Apply execution."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.cli.commands.security import session_permission_inventory
from Sprout.gateway.identity import Principal
from Sprout.security.approval import ApprovalManager
from Sprout.security.commands import CommandRegistry
from Sprout.security.policy import SecurityPolicy
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.tools.registry import ToolRegistry


def runtime_for_inventory():
    return SimpleNamespace(security=SimpleNamespace(tool_policy=SecurityPolicy(),
                           commands=CommandRegistry()), tools=ToolRegistry(),
                           _policy_engine=SimpleNamespace(_layers=()),
                           _sessions=SimpleNamespace(require_owner=AsyncMock()),
                           approvals=ApprovalManager(MemoryOperationalStore()))


@pytest.mark.asyncio
async def test_permissions_separates_live_records_from_dynamic_authority():
    runtime = runtime_for_inventory()
    manager = runtime.approvals
    common = dict(task_id="origin-task", session_id="s", requested_by="alice", source="cli",
                  resource_scope="workspace")
    approved = await manager.request("cli_tool_run", {"command": "python", "args": ["a.py"]},
                                     single_use=False, **common)
    await manager.decide(approved.id, True)
    pending = await manager.request("cli_tool_run", {"command": "python", "args": ["b.py"]},
                                    **common)
    expired = await manager.request("cli_tool_run", {"command": "python", "args": ["old.py"]},
                                    **common)
    expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await manager.store.save_approval(expired)
    other = await manager.request("cli_tool_run", {"command": "python", "args": ["secret.py"]},
                                  **{**common, "session_id": "other"})
    actor = Principal(user_id="alice", source="cli", authenticated=True)
    result = await session_permission_inventory(runtime, session_id="s", principal=actor)
    assert result["dynamic_authorization"]["status"] == "not_evaluated"
    assert [x["id"] for x in result["approved_records"]] == [approved.id]
    assert [x["id"] for x in result["pending_requests"]] == [pending.id]
    assert result["approved_records"][0]["session_exact_reuse"] is True
    assert other.id not in str(result)
    assert (await manager.store.get_approval(approved.id)).used_at is None
    assert (await manager.store.get_approval(pending.id)).status.value == "pending"
    runtime._sessions.require_owner.assert_awaited_once_with("s", actor)


@pytest.mark.asyncio
async def test_permissions_missing_or_wrong_owner_never_reads_grants():
    runtime = runtime_for_inventory()
    runtime.approvals.store.list_approvals = AsyncMock()
    with pytest.raises(PermissionError):
        await session_permission_inventory(runtime, session_id="s", principal=None)
    runtime.approvals.store.list_approvals.assert_not_awaited()
    runtime._sessions.require_owner.side_effect = PermissionError("wrong owner")
    actor = Principal(user_id="bob", source="cli", authenticated=True)
    with pytest.raises(PermissionError):
        await session_permission_inventory(runtime, session_id="s", principal=actor)
    runtime.approvals.store.list_approvals.assert_not_awaited()


@pytest.mark.asyncio
async def test_permissions_without_session_does_not_list_global_approvals():
    runtime = runtime_for_inventory()
    runtime.approvals.store.list_approvals = AsyncMock()
    result = await session_permission_inventory(runtime, session_id=None, principal=None)
    assert result["approved_records"] == [] and result["pending_requests"] == []
    runtime.approvals.store.list_approvals.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_host_proposal_without_trusted_diff_refuses_before_publication(tmp_path):
    from Sprout.orchestration.models import NodeType
    from Sprout.runtime.nodes import NodeExecutor
    from Sprout.task.models import Task
    from Sprout.workspace.models import Workspace

    node = object.__new__(NodeExecutor)
    node._project_analysis_scope = None
    node._isolated_coding_required = False
    outputs = [SimpleNamespace(type=NodeType.SANDBOX, metadata={"output": {
        "id": "sandbox", "root": str(tmp_path / "sandbox"), "kind": "git_worktree",
        "worktree_ref": "sprout-sandbox-test"}}),
        SimpleNamespace(type=NodeType.EVALUATION, metadata={"output": {"test_results": []}})]
    node._metadata = SimpleNamespace(list_execution_nodes=AsyncMock(return_value=outputs),
                                     list_change_proposals=AsyncMock(return_value=[]),
                                     save_change_proposal=AsyncMock())
    workspace = Workspace(id="w", root=tmp_path)
    task = Task(workspace_id="w", instruction="test legacy publication route")
    with pytest.raises(PermissionError, match="git_diff_unavailable"):
        await node._approval(workspace, task)

    node._metadata.save_change_proposal.assert_not_awaited()
