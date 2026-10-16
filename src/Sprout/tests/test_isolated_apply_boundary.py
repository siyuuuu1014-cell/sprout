from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.models import ChangeProposal, ChangeProposalStatus, SandboxRef
from Sprout.orchestration.models import ExecutionNode, NodeType
from Sprout.runtime.changes import ChangeProposalService
from Sprout.sandbox.lifecycle import SandboxLifecycleService
from Sprout.task.models import Task, TaskResult, TaskStatus
from Sprout.workspace.models import Workspace


@pytest.mark.parametrize("marker", ["replay_plan", "replay_source_task_id"])
@pytest.mark.parametrize("operation", ["apply", "rollback"])
async def test_persisted_replay_task_blocks_apply_even_with_force(marker, operation):
    task = Task(metadata={marker: "even-malformed-marker"})
    proposal = ChangeProposal(task_id=task.id, status=ChangeProposalStatus.APPROVED)
    service = object.__new__(ChangeProposalService)
    service._require = AsyncMock(return_value=proposal)
    service._metadata = SimpleNamespace(get_task=AsyncMock(return_value=task))
    service._apply_broker = SimpleNamespace(apply=AsyncMock(), rollback=AsyncMock())
    service._save = AsyncMock()
    if operation == "apply":
        result = await service.apply(proposal.id, force=True, override_reason="test")
    else:
        result = await service.rollback(proposal.id)
    assert not result.applied and "no host fallback" in result.reason
    service._apply_broker.apply.assert_not_called()
    service._apply_broker.rollback.assert_not_called()
    service._save.assert_not_called()


async def test_lifecycle_preserves_replay_worktree_for_all_cleanup_routes(tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    (root / "evidence.txt").write_text("keep", encoding="utf-8")
    task = Task(workspace_id="workspace", status=TaskStatus.COMPLETED, metadata={"replay_plan": {}})
    workspace = Workspace(id="workspace", root=tmp_path)
    proposal = ChangeProposal(task_id=task.id, sandbox_ref=SandboxRef("sandbox", "fixture", root))
    metadata = SimpleNamespace(get_task=AsyncMock(return_value=task),
                               list_tasks=AsyncMock(return_value=[task]),
                               list_workspaces=AsyncMock(return_value=[workspace]),
                               list_change_proposals=AsyncMock(return_value=[]),
                               list_execution_nodes=AsyncMock(return_value=[ExecutionNode(
                                   type=NodeType.SANDBOX,
                                   metadata={"output": {"root": str(root)}})]))
    service = SandboxLifecycleService(metadata)
    service._remove_refs = AsyncMock()
    service._reconcile_workspace = AsyncMock()
    await service.finalize(task, TaskResult(task_id=task.id, status=TaskStatus.COMPLETED))
    await service.cleanup_proposal(proposal)
    await service.reconcile_orphans()
    service._remove_refs.assert_not_called()
    service._reconcile_workspace.assert_not_called()
    assert (root / "evidence.txt").read_text() == "keep"
    assert root.resolve() in await service._referenced_roots()
