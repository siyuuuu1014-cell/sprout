"""Capture Git v2 artifacts and repository identity without authorizing execution."""

import hashlib
import re
import tempfile
from dataclasses import asdict
from pathlib import Path

from Sprout.execution.acceptance_producer import isolated_artifact_sources
from Sprout.execution.docker_verification import safe_path
from Sprout.execution.git_s1 import _pin_installation, _snapshot
from Sprout.execution.host_acceptance import pin_execution_directory


def capture_repository(root):
    metadata = root / ".git"
    with pin_execution_directory(root) as workspace, pin_execution_directory(metadata) as git:
        head = _snapshot(metadata, "HEAD", git.check, 4096)
        if head is None:
            raise PermissionError("git_acceptance_head_missing")
        value = head.data.decode("ascii").strip()
        if value.startswith("ref: "):
            ref = value[5:]
            if not re.fullmatch(r"refs/heads/[A-Za-z0-9_/-]+", ref):
                raise PermissionError("git_acceptance_ref_invalid")
            revision = _snapshot(metadata, ref, git.check, 128)
            if revision is None:
                raise PermissionError("git_acceptance_loose_revision_required")
            value = revision.data.decode("ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise PermissionError("git_acceptance_revision_invalid")
        index = _snapshot(metadata, "index", git.check, 4_000_000)
        if index is None:
            raise PermissionError("git_acceptance_index_missing")
        workspace.check()
        return {"root": str(root), "root_identity": list(workspace.identity),
                "git_identity": list(git.identity), "revision": value,
                "head_sha256": head.digest, "index_sha256": index.digest}


def capture_installation(root, executable):
    path = safe_path(Path(executable))
    if path.is_relative_to(root) or path.name.casefold() != "git.exe":
        raise PermissionError("git_acceptance_installation_unavailable")
    candidates = [path]
    # Git for Windows' cmd wrapper delegates to the installed native binary.
    if path.parent.name.casefold() == "cmd":
        native = path.parent.parent / "mingw64/bin/git.exe"
        candidates.append(safe_path(native))
    entries = []
    for candidate in candidates:
        if candidate.is_relative_to(root) or candidate.stat().st_size > 32_000_000:
            raise PermissionError("git_acceptance_installation_unavailable")
        with candidate.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with _pin_installation(candidate, digest, lambda: None) as pin:
            entries.append({"path": str(candidate), "sha256": digest,
                            "identity": asdict(pin["identity"])})
    return {"executable": entries[0], "chain": entries[1:]}


def capture_artifacts(workspace, acceptance, regression, bundle):
    tests, demo = isolated_artifact_sources(acceptance, regression)
    parent = safe_path(Path(tempfile.gettempdir()))
    if parent.is_relative_to(workspace):
        raise PermissionError("git_acceptance_artifact_parent_overlaps_workspace")
    directory = Path(tempfile.mkdtemp(prefix="sprout-git-acceptance-", dir=parent))
    resources = {}
    with pin_execution_directory(directory) as pin:
        for path, text, field in (
            ("frozen-commands.json", tests, "test_sha256"),
            ("frozen-demo.py", demo, "demo_sha256"),
        ):
            raw = text.encode("utf8")
            if hashlib.sha256(raw).hexdigest() != bundle[field]:
                raise PermissionError("git_acceptance_artifact_digest_changed")
            with (directory / path).open("xb") as stream:
                stream.write(raw)
            snapshot = _snapshot(directory, path, pin.check, 8_000_000)
            if snapshot is None or snapshot.digest != bundle[field] or snapshot.data != raw:
                raise PermissionError("git_acceptance_artifact_capture_changed")
            resources[path] = {"digest": snapshot.digest, "identity": asdict(snapshot.identity)}
        pin.check()
    return {"root": str(directory), "identity": list(pin.identity), "resources": resources}
