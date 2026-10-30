"""Conservative Git management for isolated experiments; no code execution API."""

import hashlib
import os
import shutil
from pathlib import Path

from Sprout.execution.git_env import git_args, git_env
from Sprout.execution.proc import run_capture
from Sprout.sandbox.git_worktree import GitWorktreeSandbox


class IsolatedGitWorktreeSandbox(GitWorktreeSandbox):
    _operations = frozenset({"worktree", "branch", "status", "add", "diff"})

    async def run_code(self, ref, language, code, *, args=()):
        raise RuntimeError("Isolated code must use the node-owned container backend")

    def _check_git_root(self, root):
        root = Path(root)
        if root.is_symlink() or not root.is_absolute():
            raise ValueError("Regular absolute Git root required")
        root = root.resolve()
        common = (self._workspace.root / ".git").resolve()
        if (self._workspace.root / ".git").is_symlink() or not common.is_dir():
            raise ValueError("Regular isolated repository Git directory required")
        marker = root / ".git"
        if marker.is_symlink():
            raise ValueError("Linked Git marker forbidden")
        if root == self._workspace.root.resolve():
            return root, common
        if not marker.is_file() or marker.stat().st_size > 4096:
            raise ValueError("Managed worktree marker required")
        value = marker.read_text(encoding="utf-8").strip()
        if not value.startswith("gitdir: ") or "\n" in value:
            raise ValueError("Invalid worktree Git marker")
        gitdir = (root / value[8:]).resolve()
        if not gitdir.is_relative_to(common / "worktrees") or not gitdir.is_dir():
            raise ValueError("Worktree Git directory escapes isolated repository")
        backlink = gitdir / "gitdir"
        if backlink.is_symlink() or not backlink.is_file() or backlink.stat().st_size > 4096:
            raise ValueError("Managed worktree backlink required")
        if Path(backlink.read_text(encoding="utf-8").strip()).resolve() != marker.resolve():
            raise ValueError("Worktree backlink mismatch")
        commondir = gitdir / "commondir"
        if (commondir.is_symlink() or not commondir.is_file()
                or commondir.stat().st_size > 4096):
            raise ValueError("Managed worktree common directory required")
        paired = commondir.read_text(encoding="utf-8").strip()
        if "\n" in paired or (gitdir / paired).resolve() != common:
            raise ValueError("Worktree common directory mismatch")
        return root, gitdir

    async def _run(self, *args):
        if args[:1] != ("git",) or "-C" not in args:
            raise ValueError("Scoped Git management command required")
        index = args.index("-C")
        root, gitdir = self._check_git_root(args[index + 1])
        resolved = shutil.which("git")
        if resolved is None:
            raise PermissionError("Host Git installation unavailable")
        executable = Path(resolved).resolve(strict=True)

        def installation_identity():
            info = executable.stat()
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                    hashlib.sha256(executable.read_bytes()).digest())

        identity = installation_identity()
        from Sprout.tools.context_keys import _current_project_node

        frame = _current_project_node()
        if frame is not None:
            task, workspace = await frame.runtime._project_analysis_subject(frame)
            if workspace != self._workspace or task.delegation_scope.is_expired():
                raise PermissionError("Host Git management subject changed")
        operation = args[index + 2]
        if operation not in self._operations:
            raise ValueError("Unsupported isolated Git management operation")
        from Sprout.execution.host_acceptance import pin_execution_directory

        with (pin_execution_directory(root) as root_pin,
              pin_execution_directory(gitdir) as metadata_pin):
            environment = git_env()
            environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                               GIT_ATTR_NOSYSTEM="1")
            pins = ("-c", "core.hooksPath=" + os.devnull, "-c", "core.attributesFile=" + os.devnull,
                    "-c", "submodule.recurse=false", "--git-dir=" + str(gitdir),
                    "--work-tree=" + str(root))
            code, stdout, _ = await run_capture(
                str(executable), *git_args(*pins, "-C", str(root), "config",
                                           "--get-regexp", r"^filter\."),
                env=environment, timeout=15)
            if code not in {0, 1}:
                raise RuntimeError("Isolated Git configuration cannot be verified")
            if stdout.strip():
                raise RuntimeError("Git filters require an explicit isolated adapter")
            if installation_identity() != identity:
                raise PermissionError("Host Git installation changed")
            if self._check_git_root(root) != (root, gitdir):
                raise PermissionError("Host Git management identity changed")
            if frame is not None:
                task, workspace = await frame.runtime._project_analysis_subject(frame)
                if workspace != self._workspace or task.delegation_scope.is_expired():
                    raise PermissionError("Host Git management subject changed")
            root_pin.check()
            metadata_pin.check()
            if args[index + 2:index + 5] == ("worktree", "remove", "--force"):
                self._check_git_root(args[index + 5])
            parts = list(args[1:])
            if operation == "diff":
                parts.insert(index + 2, "--no-textconv")
            code, stdout, _ = await run_capture(str(executable), *git_args(*pins, *parts),
                                                   env=environment, timeout=30)
            if code != 0:
                raise RuntimeError("Isolated Git management failed")
            return stdout
