"""Explicit filesystem-separated replay; not an operating-system sandbox."""

from __future__ import annotations

import copy
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from Sprout.execution.git_env import git_args, git_env


def git(repo: Path, *args: str) -> str:
    environment = git_env()
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    result = subprocess.run(
        [
            "git",
            *git_args(
                "-c",
                "core.hooksPath=" + os.devnull,
                "-c",
                "core.autocrlf=false",
                "-C",
                str(repo),
                *args,
            ),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


def prepare(source: Path, revision: str, output: Path, settings):
    """Never mutate source Git state; preserve the complete experiment directory."""
    if not revision or revision.startswith("-"):
        raise ValueError("An explicit Git revision is required")
    source, output = source.resolve(), output.resolve()
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("Replay output must not overlap the source workspace")
    commit = git(source, "rev-parse", "--verify", revision + "^{commit}")
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="growth-replay-", dir=output)).resolve()
    repo = directory / "repo"
    git(source, "clone", "--no-local", "--no-checkout", "--", str(source.resolve()), str(repo))
    git(repo, "checkout", "--detach", commit)
    if git(repo, "rev-parse", "HEAD") != commit:
        raise RuntimeError("Replay checkout revision mismatch")
    isolated = isolated_settings(directory, settings)
    return directory, repo, commit, isolated


def isolated_settings(directory: Path, settings):
    """Reassemble the same experiment authorities, never clone or create a new task."""
    isolated = copy.deepcopy(settings)
    isolated.skills_dir = str(directory / "skills")
    for slot in ("operational", "metadata", "session", "context", "usage"):
        setattr(isolated.storage, slot, f"sqlite:///{(directory / (slot + '.db')).as_posix()}")
    isolated.storage.knowledge = "memory://"
    isolated.storage.cache = "memory"
    isolated.storage.vectors = "memory"
    isolated.storage.graph = "none"
    isolated.memory.home = str(directory / "memory")
    isolated.storage.observations.enabled = False
    isolated.storage.trajectory_dir = str(directory / "trajectory")
    isolated.storage.blobs_dir = str(directory / "blobs")
    isolated.storage.project_root = str(directory / "projects")
    isolated.security.audit.path = str(directory / "audit.jsonl")
    return isolated


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()
