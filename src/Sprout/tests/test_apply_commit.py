"""Integration: apply commits the change and rollback reverts the commit."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.apply import ApplyBroker
from Sprout.execution.change import ChangeProposalBuilder
from Sprout.execution.models import (
    ApplyResult,
    ChangeProposal,
    ChangeProposalStatus,
)
from Sprout.execution.models import (
    TestResult as CaseResult,
)
from Sprout.runtime.changes import ChangeProposalService
from Sprout.sandbox.git_worktree import GitWorktreeSandbox
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task
from Sprout.workspace.models import Workspace, WorkspaceKind


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _storage(tmp_path: Path) -> StorageBundle:
    storage = StorageBundle.in_memory()
    storage.metadata = open_metadata_store(str(tmp_path / "runtime.db"))
    return storage


@pytest.mark.asyncio
async def test_apply_commits_and_rollback_reverts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "init")

    workspace = Workspace(id="ws", root=repo, kind=WorkspaceKind.GIT_REPOSITORY)
    sandbox = GitWorktreeSandbox(workspace)
    ref = await sandbox.create(branch="sprout-sandbox-commit")
    try:
        (ref.root / "tracked.txt").write_text("two\n", encoding="utf-8")
        (ref.root / "new.py").write_text("print('hi')\n", encoding="utf-8")

        diff = await sandbox.diff(ref)
        files_changed = await sandbox.changed_files(ref)
        proposal = ChangeProposalBuilder().build(
            task_id="task-commit",
            sandbox_ref=ref,
            files_changed=files_changed,
            test_results=(CaseResult(name="test: pytest -q", passed=True),),
            diffs=(diff,),
            risk="medium",
        )
        proposal = replace(proposal, metadata={"summary": "add new feature"})

        storage = _storage(tmp_path)
        await storage.metadata.save_workspace(workspace)
        await storage.metadata.save_task(Task(id="task-commit", workspace_id="ws"))
        await storage.metadata.save_change_proposal(proposal)

        approvals = ApprovalManager(storage.operational)
        apply_broker = ApplyBroker(PolicyEngine(), approvals=approvals)
        service = ChangeProposalService(
            storage.metadata,
            storage.operational,
            apply_broker,
            approvals=approvals,
        )
        approved = await service.approve(proposal.id, decided_by="test")
        assert approved.status is ChangeProposalStatus.APPROVED

        # This legacy fixture has only a generic check, not requirement cases.
        # Ordinary apply must refuse; commit/rollback exercise an audited override.
        blocked = await service.apply(proposal.id)
        assert not blocked.applied and "legacy proposal" in blocked.reason
        applied = await service.apply(
            proposal.id, force=True, override_reason="Legacy commit/rollback integration fixture",
            decided_by="test",
        )
        assert applied.applied is True, applied.reason
        assert (repo / "tracked.txt").read_text(encoding="utf-8") == "two\n"
        assert (repo / "new.py").read_text(encoding="utf-8") == "print('hi')\n"
        assert "add new feature" in _git(repo, "log", "-1", "--oneline")

        stored = await storage.metadata.get_change_proposal(proposal.id)
        assert stored is not None
        assert stored.metadata.get("applied_commit")
        assert stored.metadata.get("base_commit")

        rolled_back = await service.rollback(proposal.id)
        assert rolled_back.applied is True, rolled_back.reason
        assert "Revert" in _git(repo, "log", "-1", "--oneline")
        storage.metadata.close()
    finally:
        await sandbox.remove(ref)


async def test_failed_revert_never_attempts_reset_or_reports_success(tmp_path):
    broker = ApplyBroker(PolicyEngine())
    proposal = ChangeProposal(task_id="fixture", metadata={
        "applied_commit": "b" * 40, "base_commit": "a" * 40})
    broker.evaluate = AsyncMock(return_value=ApplyResult(proposal.id, applied=True))
    broker._git = AsyncMock(return_value=(1, "", "revert conflict"))
    workspace = Workspace(id="fixture", root=tmp_path)
    result = await broker.rollback(proposal, workspace)
    assert not result.applied and "manual reconciliation" in result.reason
    broker._git.assert_awaited_once_with(workspace, "revert", "--no-edit", "b" * 40)


async def test_failed_real_revert_preserves_user_changes_and_proposal_state(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    tracked = repo / "tracked.txt"
    tracked.write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "baseline")
    base = _git(repo, "rev-parse", "HEAD").strip()
    tracked.write_text("applied feature\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "fixture applied")
    applied = _git(repo, "rev-parse", "HEAD").strip()
    tracked.write_text("operator uncommitted work\n", encoding="utf-8")
    notes = repo / "notes.txt"
    notes.write_text("operator untracked notes\n", encoding="utf-8")
    before_index = _git(repo, "diff", "--cached")
    workspace = Workspace(id="fixture", root=repo, kind=WorkspaceKind.GIT_REPOSITORY)
    proposal = ChangeProposal(task_id="fixture-task", status=ChangeProposalStatus.APPLIED,
                              metadata={"base_commit": base, "applied_commit": applied})
    storage = _storage(tmp_path)
    try:
        await storage.metadata.save_workspace(workspace)
        await storage.metadata.save_task(Task(id=proposal.task_id, workspace_id=workspace.id))
        await storage.metadata.save_change_proposal(proposal)
        policy = SimpleNamespace(decide=lambda request: PolicyDecision(
            AccessDecision.ALLOW, "explicit test-owned rollback fixture"))
        service = ChangeProposalService(storage.metadata, storage.operational, ApplyBroker(policy))
        result = await service.rollback(proposal.id)
        assert not result.applied and "manual reconciliation" in result.reason
        assert tracked.read_text() == "operator uncommitted work\n"
        assert notes.read_text() == "operator untracked notes\n"
        assert _git(repo, "rev-parse", "HEAD").strip() == applied
        assert _git(repo, "diff", "--cached") == before_index
        saved = await storage.metadata.get_change_proposal(proposal.id)
        assert saved.status is ChangeProposalStatus.APPLIED
    finally:
        storage.metadata.close()
