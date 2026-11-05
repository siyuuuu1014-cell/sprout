"""Publication origin contracts with controlled native opens; native linked test separate."""

import hashlib
from contextlib import contextmanager
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution import git_s1 as s
from Sprout.execution.file_snapshot import ResourceIdentity


@pytest.mark.parametrize(
    "case",
    [
        "linked",
        "ordinary",
        "external-pointer",
        "bad-back",
        "bad-common",
        "similar-root",
        "extra-external",
        "forged-pointer",
        "replaced-pointer",
        "head",
        "index",
        "source",
    ],
)
async def test_publication_metadata_origin(tmp_path, monkeypatch, case):
    root = tmp_path / "sandbox"
    common = root / ".git" if case == "ordinary" else tmp_path / "common"
    meta = common if case == "ordinary" else common / "worktrees/task"
    baseline = "a" * 40
    binding = SimpleNamespace(
        sandbox=SimpleNamespace(root=root),
        metadata=meta,
        common=common,
        baseline=baseline,
        root_identity="unit",
        paths=("src/app.py",),
    )
    values = {
        (meta, "HEAD"): b"ref: refs/heads/main\n",
        (meta, "index"): b"index",
        (common, "refs/heads/main"): (baseline + "\n").encode(),
        (root, "src/app.py"): b"source",
    }
    if case != "ordinary":
        values.update(
            {
                (root, ".git"): b"gitdir: " + str(meta).encode() + b"\n",
                (meta, "commondir"): b"../..\n",
                (meta, "gitdir"): str(root / ".git").encode() + b"\n",
            }
        )
    reads = []
    replaced = [False]

    def snapshot(base, relative, authorize, limit=1_000_000):
        authorize()
        reads.append((base, relative))
        data = next((v for (b, n), v in values.items() if b / n == base / relative), None)
        if data is None:
            return None
        inode = 99 if replaced[0] and base / relative == root / ".git" else 2
        identity = ResourceIdentity(1, inode, len(data), 3, 4)
        return SimpleNamespace(
            data=data, digest=hashlib.sha256(data).hexdigest(), identity=identity
        )

    @contextmanager
    def pin(path):
        yield SimpleNamespace(identity="unit", check=lambda: None)

    monkeypatch.setattr(s, "_snapshot", snapshot)
    monkeypatch.setattr("Sprout.execution.host_acceptance.pin_execution_directory", pin)
    _, metadata, _ = s._source(binding, lambda: None)
    source = snapshot(root, "src/app.py", lambda: None)
    evidence = {
        "baseline": baseline,
        "metadata": metadata,
        "current_files": {
            "src/app.py": {
                "sha256": source.digest,
                "identity": asdict(source.identity),
                "size": len(source.data),
            }
        },
    }
    if case == "replaced-pointer":
        replaced[0] = True
    if case == "external-pointer":
        values[root, ".git"] = b"gitdir: /outside\n"
    if case == "bad-back":
        values[meta, "gitdir"] = b"/outside/.git\n"
    if case == "bad-common":
        values[meta, "commondir"] = b"/outside\n"
    if case in {"similar-root", "extra-external"}:
        path = tmp_path / ("sandbox-copy/.git" if case == "similar-root" else "outside/HEAD")
        evidence["metadata"][str(path)] = {"sha256": "f" * 64, "identity": {}}
    if case == "forged-pointer":
        evidence["metadata"][str(root / ".git")]["sha256"] = "f" * 64
    if case == "head":
        values[meta, "HEAD"] = b"b" * 40 + b"\n"
    if case == "index":
        values[meta, "index"] = b"changed-index"
    if case == "source":
        values[root, "src/app.py"] = b"changed-source"
    issuer = SimpleNamespace(_git_review_record=AsyncMock())
    frame = SimpleNamespace(task=SimpleNamespace())
    broker = SimpleNamespace(_authorize_review=AsyncMock(), _check_review_policy=lambda *a: None)
    if case in {"linked", "ordinary"}:
        await s.validate_review(binding, issuer, frame, broker, evidence)
    else:
        with pytest.raises(PermissionError):
            await s.validate_review(binding, issuer, frame, broker, evidence)
    assert not any(str(base / relative).endswith("sandbox-copy/.git") for base, relative in reads)
