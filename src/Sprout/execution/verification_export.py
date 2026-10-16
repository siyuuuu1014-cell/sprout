"""Export dedicated verification inputs without Git/runtime/private resources."""

import hashlib
import os
from pathlib import Path

from Sprout.execution.docker_verification import safe_path
from Sprout.workspace.classifier import classify
from Sprout.workspace.models import ResourceKind

EXCLUDED = {
    ".git",
    ".venv",
    ".ssh",
    ".sprout",
    ".workbuddy",
    ".codex",
    ".agents",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}


def private_resource(relative, root, overrides=None):
    denied = {ResourceKind.SECRET, ResourceKind.SENSITIVE, ResourceKind.DATA, ResourceKind.EXTERNAL}
    return (
        classify(relative, workspace_root=root) in denied
        or classify(relative, workspace_root=root, overrides=overrides) in denied
    )


def export_input(
    repo: Path, destination: Path, original: Path, *, classify_overrides=None, exclude_paths=(),
    manifest_only=False
) -> dict[str, str]:
    repo, original = safe_path(repo), safe_path(original)
    exclusions = []
    for name in exclude_paths:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("Export exclusions must be confined relative paths")
        exclusions.append(relative)

    def excluded(path):
        relative = path.relative_to(repo)
        return any(relative == name or relative.is_relative_to(name) for name in exclusions)
    destination = destination.resolve()
    if any(
        destination.is_relative_to(root) or root.is_relative_to(destination)
        for root in (repo, original)
    ):
        raise ValueError("Export destination must not overlap input or original workspace")
    if not manifest_only:
        destination.mkdir(parents=True, exist_ok=False)
    manifest = {}
    total = 0
    for parent, directories, files in os.walk(repo, followlinks=False):
        for name in (*directories, *files):
            path = Path(parent) / name
            if path.is_symlink() or not path.resolve().is_relative_to(repo):
                raise ValueError("Export input contains a link or external resource")
        directories[:] = [name for name in directories
                          if name.casefold() not in EXCLUDED and not excluded(Path(parent) / name)]
        for name in files:
            path = Path(parent) / name
            relative = path.relative_to(repo)
            if name.casefold() in EXCLUDED or excluded(path):
                continue
            if private_resource(relative, repo, classify_overrides):
                raise ValueError("Private/data input requires an explicit supported adapter")
            content = path.read_bytes()
            total += len(content)
            if total > 32 * 1024 * 1024 or len(manifest) >= 10000:
                raise ValueError("Verification export size limit exceeded")
            if not manifest_only:
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            manifest[relative.as_posix()] = hashlib.sha256(content).hexdigest()
    if not manifest:
        raise ValueError("Verification export must not be empty")
    return manifest
