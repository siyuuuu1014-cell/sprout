"""Sandbox lifecycle finalization and orphan reconciliation."""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from pathlib import Path

from Sprout.execution.models import ChangeProposal, ChangeProposalStatus, SandboxRef
from Sprout.execution.replay_boundary import isolated_replay_task
from Sprout.orchestration.models import NodeType
from Sprout.sandbox.filesystem import KIND as FILESYSTEM_KIND
from Sprout.sandbox.git_worktree import GitWorktreeSandbox
from Sprout.sandbox.isolated_git_worktree import IsolatedGitWorktreeSandbox
from Sprout.storage.contracts.metadata import MetadataStore
from Sprout.task.models import Task, TaskResult, TaskStatus
from Sprout.workspace.models import Workspace

logger = logging.getLogger("sprout.sandbox.lifecycle")

_TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}


def _missing(root: Path) -> bool:
    try:
        root.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        # An inaccessible path is not evidence that it was removed.
        logger.warning("Sandbox path unavailable: %s", root)
    return False


class SandboxLifecycleService:
    """Removes sandbox worktrees when tasks no longer need them."""

    def __init__(self, metadata: MetadataStore | None) -> None:
        self._metadata = metadata
        self.filesystem = {}

    def close_filesystem(self, task_id):
        experiment = self.filesystem.get(task_id)
        if experiment is not None:
            experiment.sandbox.close()
            del self.filesystem[task_id]

    def close_filesystem_all(self):
        for task_id in tuple(self.filesystem):
            try:
                self.close_filesystem(task_id)
            except (OSError, ValueError):
                logger.exception("Filesystem sandbox cleanup refused for task %s", task_id)

    async def finalize(self, task: Task, result: TaskResult) -> None:
        """Clean terminal task sandboxes.

        Waiting approval tasks keep their worktrees because apply/rollback may
        still need the branch and its pending diff.
        """
        if isolated_replay_task(task) or result.status not in _TERMINAL_TASK_STATUSES:
            return
        if task.id in self.filesystem:
            self.close_filesystem(task.id)
        if self._metadata is not None:
            for proposal in await self._metadata.list_change_proposals(task.id):
                await self.cleanup_proposal(proposal)

    async def cleanup_proposal(self, proposal: ChangeProposal) -> None:
        """Remove a single proposal's sandbox after reject/apply/rollback."""
        if proposal.sandbox_ref is None:
            return
        task = await self._task(proposal.task_id)
        if task is None:
            return
        if isolated_replay_task(task):
            return
        workspace = await self._workspace_for_task(task)
        if workspace is None:
            return
        ref = proposal.sandbox_ref
        experiment = self.filesystem.get(task.id)
        if (ref.kind == FILESYSTEM_KIND and experiment is not None
                and experiment.sandbox.ref == ref):
            self.close_filesystem(task.id)
        elif not await self._remove_ref(workspace, ref):
            return
        await self._settle_ref(proposal)

    async def reconcile_orphans(self) -> None:
        """Remove sandbox worktrees that no active proposal references."""
        if self._metadata is None:
            return
        tasks = await self._metadata.list_tasks()
        referenced = await self._referenced_roots()
        preserved = {task.workspace_id for task in tasks if isolated_replay_task(task)}
        workspaces = defaultdict(list)
        for workspace in await self._metadata.list_workspaces():
            if workspace.id not in preserved:
                workspaces[Path(workspace.root).resolve().parent].append(workspace)
        for parent, candidates in workspaces.items():
            await self._reconcile_parent(parent, candidates, referenced)

    async def _settle_ref(self, proposal: ChangeProposal) -> None:
        # Exact Apply authenticates even idempotent requests against the full
        # original proposal. Its SandboxRef is immutable evidence.
        if proposal.metadata.get("apply_plan_sha256") is not None:
            return
        if proposal.status not in {
            ChangeProposalStatus.APPLIED, ChangeProposalStatus.REJECTED,
            ChangeProposalStatus.ROLLED_BACK,
        }:
            return
        clear = getattr(self._metadata, "clear_cleaned_sandbox", None)
        if clear is not None:
            await clear(proposal)

    async def _workspace_for_task(self, task: Task) -> Workspace | None:
        if self._metadata is None:
            return None
        return await self._metadata.get_workspace(task.workspace_id)

    async def _task(self, task_id: str) -> Task | None:
        if self._metadata is None:
            return None
        return await self._metadata.get_task(task_id)

    async def _referenced_roots(self) -> set[Path]:
        referenced: set[Path] = set()
        if self._metadata is None:
            return referenced
        for task in await self._metadata.list_tasks():
            for proposal in await self._metadata.list_change_proposals(task.id):
                if proposal.sandbox_ref is not None:
                    root = Path(proposal.sandbox_ref.root)
                    if _missing(root):
                        if not isolated_replay_task(task):
                            await self._settle_ref(proposal)
                        continue
                    referenced.add(root.resolve())
            # A task can park on an *approval* before it has minted a change
            # proposal (the verification command asks a human). Its sandbox is
            # the only place the resumed work can run, so it must be treated as
            # referenced even though no proposal row exists yet.
            if task.status in _TERMINAL_TASK_STATUSES and not isolated_replay_task(task):
                continue
            for node in await self._metadata.list_execution_nodes(task.id):
                if node.type is not NodeType.SANDBOX:
                    continue
                output = node.metadata.get("output")
                if isinstance(output, dict) and output.get("root"):
                    referenced.add(Path(str(output["root"])).resolve())
        return referenced

    async def _reconcile_parent(self, parent, workspaces, referenced):
        for path in parent.glob(".sprout-sprout-sandbox-*"):
            if not path.is_dir() or path in referenced:
                continue
            marker = path / ".git"
            if _missing(marker):
                await self._warn_markerless(path)
                continue
            if not marker.is_file() or marker.is_symlink():
                logger.warning("Preserving sandbox candidate %s: invalid_worktree_marker", path)
                continue
            # A marker is only a hint. Verify the reciprocal management links
            # against each registered repository before invoking any Git cleanup.
            for workspace in workspaces:
                if not (workspace.root / ".git").is_dir():
                    continue
                try:
                    IsolatedGitWorktreeSandbox(workspace)._check_git_root(path)
                except (OSError, ValueError):
                    continue
                ref = SandboxRef(
                    path.name, "git_worktree", path, path.name.removeprefix(".sprout-")
                )
                await self._remove_ref(workspace, ref)
                break
            else:
                logger.warning("Preserving sandbox candidate %s: worktree_owner_unverified", path)

    async def _warn_markerless(self, path: Path) -> None:
        record = getattr(self._metadata, "record_markerless_sandbox", None)
        if record is not None:
            try:
                info = path.lstat()
                fingerprint = (
                    f"{info.st_dev}:{info.st_ino}:{info.st_ctime_ns}:{info.st_mtime_ns}"
                )
                if not await record(path, fingerprint):
                    return
            except FileNotFoundError:
                return
            except (OSError, sqlite3.Error):
                # Diagnostics are optional. A failed record never permits removal.
                pass
        logger.warning("Preserving sandbox candidate %s: missing_worktree_marker", path)

    async def _remove_ref(self, workspace: Workspace, ref: SandboxRef) -> bool:
        if _missing(ref.root):
            return True
        if ref.kind != "git_worktree":
            logger.warning(
                "Preserving sandbox %s: no live cleanup owner for %s", ref.root, ref.kind
            )
            return False
        try:
            await GitWorktreeSandbox(workspace).remove(ref)
        except (OSError, ValueError, RuntimeError):
            logger.exception("Failed to remove sandbox %s", ref.root)
            return False
        return _missing(ref.root)
