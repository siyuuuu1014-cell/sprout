"""Operator-selected test/config integrity snapshots, not an OS trust boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def snapshot(root: Path, paths: tuple[str, ...]) -> dict[str, str]:
    root = root.resolve()
    if not paths:
        raise ValueError("Explicit oracle test/config paths are required")
    files = {}
    for name in paths:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("Oracle paths must be relative and confined to the repository")
        target = root / relative
        # Reject links in ancestors too; resolving a link does not freeze its identity.
        if any(
            (root / Path(*relative.parts[:i])).is_symlink()
            for i in range(1, len(relative.parts) + 1)
        ):
            raise ValueError("Oracle paths must not contain symbolic links")
        if not target.exists():
            raise ValueError(f"Oracle path is missing: {name}")
        entries = [target] if target.is_file() else sorted(target.rglob("*"))
        for entry in entries:
            if entry.is_symlink() or not entry.resolve().is_relative_to(root):
                raise ValueError("Oracle paths must not contain symbolic links")
            if entry.is_file():
                files[entry.relative_to(root).as_posix()] = hashlib.sha256(
                    entry.read_bytes()
                ).hexdigest()
        if not any(
            key == relative.as_posix() or key.startswith(relative.as_posix() + "/") for key in files
        ):
            raise ValueError(f"Oracle path has no files: {name}")
    return dict(sorted(files.items()))


def digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def integrity(root: Path, paths: tuple[str, ...], frozen: dict[str, str]) -> bool:
    try:
        return snapshot(root, paths) == frozen
    except (ValueError, OSError):
        return False
