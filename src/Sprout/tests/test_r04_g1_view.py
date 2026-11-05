"""G1 bounded metadata/authority tests. No Git processes or native-pass claim."""

import hashlib
import zlib
from copy import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from Sprout.execution import git_s1
from Sprout.execution.file_snapshot import ResourceIdentity, SnapshotResult
from Sprout.task.models import DelegationScope
from Sprout.tests.test_r04_f3_manifest import execution as execution
from Sprout.tests.test_r04_f3_manifest import f3 as f3
from Sprout.tests.test_r04_f3_manifest import rig as rig


def objects(mode=b"100644"):
    store, calls = {}, []

    def put(kind, data):
        body = kind + b" " + str(len(data)).encode() + b"\0" + data
        oid = hashlib.sha1(body).hexdigest()
        store[oid] = zlib.compress(body)
        return oid

    selected = put(b"blob", b"selected content")
    private = put(b"blob", b"must not be captured")
    tree = put(
        b"tree",
        mode
        + b" selected.py\0"
        + bytes.fromhex(selected)
        + b"100644 private.py\0"
        + bytes.fromhex(private),
    )
    base = put(b"commit", b"tree " + tree.encode() + b"\n\nfixture\n")

    def read(root, path, limit):
        oid = path.split("/")[-2] + path.split("/")[-1]
        calls.append(oid)
        return None if oid not in store else SimpleNamespace(data=store[oid])

    binding = SimpleNamespace(common="unit", baseline=base)
    return SimpleNamespace(**locals())


def test_selected_baseline_never_reads_unselected_blob():
    o = objects()
    assert git_s1._LooseObjects(o.binding, o.read).file("selected.py") == b"selected content"
    assert o.private not in o.calls


@pytest.mark.parametrize("mode", [b"120000", b"160000", b"40000"])
def test_nonregular_selected_blob_refused(mode):
    o = objects(mode)
    with pytest.raises(PermissionError):
        git_s1._LooseObjects(o.binding, o.read).file("selected.py")
    assert o.selected not in o.calls


@pytest.mark.parametrize("change", ["missing", "hash", "size", "compressed_budget", "oid"])
def test_object_source_failure_is_not_empty_diff(change):
    o = objects()
    if change == "missing":
        o.store.pop(o.selected)
    if change == "hash":
        o.store[o.selected] = zlib.compress(b"blob 4\0fake")
    if change == "size":
        o.store[o.selected] = zlib.compress(b"blob 999\0x")
    if change == "compressed_budget":
        o.store[o.selected] = zlib.compress(b"blob 9000000\0" + b"x" * 3000000)
    if change == "oid":
        o.binding.baseline = "../outside"
    with pytest.raises(PermissionError):
        git_s1._LooseObjects(o.binding, o.read).file("selected.py")
    assert o.private not in o.calls


@pytest.mark.parametrize(
    "change",
    [
        "valid",
        "external_pointer",
        "external_common",
        "wrong_back",
        "wrong_head",
        "missing_index",
        "bad_ref",
    ],
)
def test_worktree_management_pairing(tmp_path, monkeypatch, change):
    root = tmp_path / "worktree"
    meta = tmp_path / "common/worktrees/fixture"
    common = tmp_path / "common"
    base = "a" * 40
    binding = SimpleNamespace(
        sandbox=SimpleNamespace(root=root), metadata=meta, common=common, baseline=base
    )
    values = {
        (root, ".git"): b"gitdir: " + str(meta).encode() + b"\n",
        (meta, "commondir"): b"../..\n",
        (meta, "gitdir"): str(root / ".git").encode() + b"\n",
        (meta, "HEAD"): b"ref: refs/heads/fixture\n",
        (meta, "index"): b"unit-index",
        (common, "refs/heads/fixture"): base.encode() + b"\n",
    }
    if change == "external_pointer":
        values[root, ".git"] = b"gitdir: C:/other\n"
    if change == "external_common":
        values[meta, "commondir"] = b"../../../outside\n"
    if change == "wrong_back":
        values[meta, "gitdir"] = str(root.parent / "other/.git").encode()
    if change == "wrong_head":
        values[common, "refs/heads/fixture"] = b"b" * 40
    if change == "missing_index":
        values.pop((meta, "index"))
    if change == "bad_ref":
        values[meta, "HEAD"] = b"ref: refs/heads/../outside\n"
    calls = []

    def snapshot(parent, path, authorize, limit):
        authorize()
        calls.append((parent, path))
        data = values.get((parent, path))
        return (
            None
            if data is None
            else SnapshotResult(
                True,
                data=data,
                digest=hashlib.sha256(data).hexdigest(),
                identity=ResourceIdentity(1, 1, len(data), 1, 1),
            )
        )

    monkeypatch.setattr(git_s1, "_snapshot", snapshot)
    if change == "valid":
        assert git_s1._source(binding, lambda: None)[0] == b"unit-index"
    else:
        with pytest.raises(PermissionError):
            git_s1._source(binding, lambda: None)
    assert all(parent in {root, meta, common} for parent, _ in calls)


@pytest.fixture
async def binding(f3):
    e = f3
    task = replace(
        e.rig.task,
        delegation_scope=replace(
            e.rig.task.delegation_scope,
            allowed_actions=e.rig.task.delegation_scope.allowed_actions | {"git.diff"},
        ),
    )
    await e.rig.storage.metadata.save_task(task)
    (e.root / ".git").mkdir()
    program = e.root.parent / "git.exe"
    program.write_bytes(b"never executed")
    await e.rig.runtime.bind_task_git_view(
        task.id,
        task.actor,
        executable=program,
        executable_sha256=hashlib.sha256(program.read_bytes()).hexdigest(),
        baseline="a" * 40,
        metadata_root=e.root / ".git",
        paths=("src/app.py",),
    )
    value = e.rig.runtime._verification_hosts[task.id]["git"]
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("change", ["valid", "copy", "registry_lost", "expired", "owner", "scope"])
async def test_view_data_cannot_supply_authority(binding, monkeypatch, change):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock

    b = binding
    e = b.e
    runtime = e.rig.runtime
    value = copy(b.value) if change == "copy" else b.value
    if change == "registry_lost":
        runtime._verification_hosts[b.task.id].pop("git")
    if change in {"expired", "scope"}:
        scope = (
            replace(b.task.delegation_scope, expires_at=datetime.now(UTC) - timedelta(seconds=1))
            if change == "expired"
            else DelegationScope()
        )
        await e.rig.storage.metadata.save_task(replace(b.task, delegation_scope=scope))
    if change == "owner":
        original = e.rig.storage.metadata.get_execution_node

        async def wrong(identifier):
            node = await original(identifier)
            if identifier == e.rig.node.id:
                return replace(
                    node,
                    metadata={
                        **node.metadata,
                        "execution_guard": {**node.metadata["execution_guard"], "owner": "other"},
                    },
                )
            return node

        monkeypatch.setattr(e.rig.storage.metadata, "get_execution_node", wrong)
    spawn = AsyncMock(side_effect=AssertionError("No process in guarded tests"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    with runtime._project_analysis_scope(b.task, e.rig.workspace, e.rig.node) as frame:
        call = runtime._git_review_record(value, runtime._git_broker, frame)
        if change == "valid":
            assert (await call)[0].id == b.task.id
        else:
            with pytest.raises(PermissionError):
                await call
    spawn.assert_not_called()


async def test_view_extra_file_rejected_before_any_content_read(tmp_path, monkeypatch):
    from unittest.mock import Mock

    async def listing(root, target, limit, authorize):
        await authorize(root / "unselected.py")
        raise AssertionError("Unexpected entry accepted")

    from Sprout.execution import file_access

    monkeypatch.setattr(file_access, "directory_entries", listing)
    read = Mock(side_effect=AssertionError("Extra content must not be opened"))
    monkeypatch.setattr(git_s1, "_snapshot", read)

    async def refresh():
        pass

    with pytest.raises(PermissionError, match="git_view_unselected_entry"):
        await git_s1._check_review_files(tmp_path, {"selected.py": b"x"}, refresh, lambda: None)
    read.assert_not_called()


@pytest.mark.parametrize("kind", ["size", "type", "missing"])
def test_explicit_object_validation_never_invokes_git(kind, monkeypatch):
    from unittest.mock import AsyncMock

    program = AsyncMock(side_effect=AssertionError("No Git fallback for object failure"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", program)
    value = b"blob 999\0x" if kind == "size" else b"tree 1\0x"
    oid = hashlib.sha1(value).hexdigest()

    def reader(*args):
        return None if kind == "missing" else SimpleNamespace(data=zlib.compress(value))

    with pytest.raises(PermissionError):
        git_s1._LooseObjects(SimpleNamespace(common="unit"), reader).object(oid, "blob")
    program.assert_not_called()


@pytest.mark.parametrize("change", ["view", "owner", "arguments", "lost"])
async def test_command_data_does_not_grant_execution(binding, monkeypatch, change):
    import asyncio
    from unittest.mock import AsyncMock

    b = binding
    e = b.e
    runtime = e.rig.runtime
    program = AsyncMock(side_effect=AssertionError("No arbitrary Git operation"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", program)

    async def untrusted_callback():
        pass

    with runtime._project_analysis_scope(b.task, e.rig.workspace, e.rig.node) as frame:
        active = {
            "binding": b.value,
            "frame": frame,
            "owner": asyncio.current_task(),
            "view": e.root,
        }
        if change == "owner":
            active["owner"] = object()
        host = runtime._verification_hosts[b.task.id]
        if change != "lost":
            host["git_active"] = active
        try:
            with pytest.raises(PermissionError):
                await git_s1._review_command(
                    b.value,
                    e.root.parent if change == "view" else e.root,
                    ("diff", "--ext-diff", "--textconv"),
                    untrusted_callback,
                    issuer=runtime,
                    frame=frame,
                    broker=runtime._git_broker,
                )
        finally:
            host.pop("git_active", None)
    program.assert_not_called()
