"""Read-only Git transaction preflight; this module grants no write authority."""

import hashlib
import re
from pathlib import Path

from Sprout.execution.docker_verification import safe_path
from Sprout.execution.verification_export import export_input
from Sprout.sandbox.isolated_git_worktree import IsolatedGitWorktreeSandbox


class IsolatedGitPlan(IsolatedGitWorktreeSandbox):
    _operations = frozenset({"status", "diff", "rev-parse", "rev-list", "ls-files"})

    def __init__(self, workspace, *, original, evidence):
        super().__init__(workspace)
        self.original, self.evidence = safe_path(original), safe_path(evidence)
        root = safe_path(workspace.root)
        if (root != self.evidence / "repo" or any(
                root.is_relative_to(path) or path.is_relative_to(root)
                for path in (self.original,))
                or self.evidence.is_relative_to(self.original)
                or self.original.is_relative_to(self.evidence)):
            raise ValueError("Independent experiment repo and original required")

    @property
    def repository_root(self):
        return self._workspace.root.resolve()

    async def _head(self, root):
        value = (await self._run("git", "-C", str(root), "rev-parse", "--verify",
                                 "HEAD^{commit}")).strip()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
            raise ValueError("Full Git commit identity required")
        return value

    async def _clean_target(self):
        root = self._workspace.root
        if (await self._run("git", "-C", str(root), "status", "--porcelain", "-z",
                            "--untracked-files=all")).strip():
            raise ValueError("Experiment target contains unrelated changes")
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "sequencer"):
            if (root / ".git" / name).exists():
                raise ValueError("Unfinished Git operation requires manual reconciliation")

    async def commit_plan(self, sandbox_root):
        await self._clean_target()
        base = await self._head(self._workspace.root)
        source = safe_path(Path(sandbox_root))
        self._check_git_root(source)
        if source == self._workspace.root.resolve() or await self._head(source) != base:
            raise ValueError("Managed sandbox must share target baseline")
        if (await self._run("git", "-C", str(source), "ls-files", "--others",
                            "--exclude-standard", "-z")).strip():
            raise ValueError("Untracked files must be explicitly represented in the reviewed diff")
        diff = await self._run("git", "-C", str(source), "diff", "--no-ext-diff",
                               "--binary", "HEAD", "--")
        manifest = export_input(source, self.evidence / "commit-preview", self.original,
                                manifest_only=True)
        return {"schema_version": 1, "operation": "commit", "base_commit": base,
                "target": str(self._workspace.root.resolve()), "source": str(source),
                "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
                "source_manifest": manifest, "write_authorized": False}

    async def rollback_plan(self, *, applied_commit, base_commit):
        for value in (applied_commit, base_commit):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
                raise ValueError("Recorded full commit identities required")
        await self._clean_target()
        if await self._head(self._workspace.root) != applied_commit:
            raise ValueError("Target moved after apply; manual reconciliation required")
        parents = (await self._run("git", "-C", str(self._workspace.root), "rev-list",
                                  "--parents", "-n", "1", applied_commit)).split()
        if parents != [applied_commit, base_commit]:
            raise ValueError("Rollback requires the recorded single-parent commit")
        return {"schema_version": 1, "operation": "rollback",
                "target": str(self._workspace.root.resolve()), "applied_commit": applied_commit,
                "base_commit": base_commit, "strategy": "new_revert_commit",
                "write_authorized": False, "invalidates_current_validation": True}
