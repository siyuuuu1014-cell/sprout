"""B2-1 internal broker snapshots; never enables ProjectAnalyze."""

import asyncio
import hashlib
import os
from copy import deepcopy
from dataclasses import replace
from threading import Event
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.execution import file_broker
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.file_snapshot import (
    ResourceIdentity,
    SnapshotResult,
    _ReadOptions,
    current_read_subject,
    read_fd,
)
from Sprout.execution.models import SandboxRef
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.security.approval import ApprovalManager, ApprovalRecord, ApprovalStatus
from Sprout.security.engine import PolicyEngine
from Sprout.task.models import DelegationScope
from Sprout.tests.test_r04_pa2_b1_binding import issued, node_scope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.tools import context_keys as keys


def fixture_snapshot(data=b"fixture", **changes):
    result = SnapshotResult(
        True, data=data, digest=hashlib.sha256(data).hexdigest(),
        identity=ResourceIdentity(1, 2, len(data), 0, 0),
    )
    return replace(result, **changes)


@pytest.fixture(autouse=True)
async def unchanged_approval_storage(rig, monkeypatch):
    store = rig.storage.operational
    for single_use in (True, False):
        await store.save_approval(ApprovalRecord(
            tool="file.read", arguments_fingerprint="controlled-fixture",
            status=ApprovalStatus.APPROVED, task_id=rig.task.id, single_use=single_use,
            requested_by=rig.principal.user_id, session_id="real-session", source="cli",
        ))
    before = deepcopy(await store.list_approvals())
    spies = []
    for name in ("save_approval", "save_approval_if_status"):
        spy = AsyncMock(side_effect=AssertionError("snapshot wrote approval storage"))
        monkeypatch.setattr(store, name, spy)
        spies.append(spy)
    yield
    assert await store.list_approvals() == before
    for spy in spies:
        spy.assert_not_called()


@pytest.fixture(autouse=True)
def no_approval_activity(monkeypatch):
    spies = []
    for name in ("request", "is_approved"):
        spy = AsyncMock(side_effect=AssertionError("snapshot reached approval"))
        monkeypatch.setattr(ApprovalManager, name, spy)
        spies.append(spy)
    yield
    for spy in spies:
        spy.assert_not_called()


def test_snapshot_api_exists():
    assert callable(getattr(FileBroker, "read_snapshot", None))


@pytest.mark.asyncio
async def test_enumeration_is_explicitly_unavailable():
    broker = FileBroker(PolicyEngine())
    result = await broker.enumerate_readable()
    assert not result.ok
    assert result.reason == "directory_enumeration_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("argument", ["a.py", {"trusted": True, "actor": "owner"}])
async def test_unbound_or_model_subject_cannot_read(tmp_path, monkeypatch, argument):
    backend = Mock(side_effect=AssertionError("backend reached"))
    monkeypatch.setattr(file_broker, "file_operation", backend)
    result = await FileBroker(PolicyEngine()).read_snapshot(
        SandboxRef("w", "test", tmp_path), argument, subject={}, max_bytes=100)
    assert not result.ok
    backend.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "allow", "nested", "denied", "expired", "file_deny", "redacted", "secret",
    "sensitive", "data", "external", "unclassified", "budget", "identity", "revoked",
    "forged_actor", "forged_task", "forged_scope",
])
async def test_bound_read_contract(rig, monkeypatch, case):
    from Sprout.execution.file_access import FileBoundaryError

    root = rig.root
    directory = root / "src"
    directory.mkdir()
    path = directory / "a.py"
    path.write_bytes(b"x = 1\r\n")
    relative = "src/a.py"
    if case == "nested":
        path = directory / "nested" / "allowed.py"
        path.parent.mkdir()
        path.write_bytes(b"x = 1\r\n")
        relative = "src/nested/allowed.py"
    policy = PolicyEngine()
    if case == "denied":
        relative = "src/private/a.py"
    if case == "external":
        relative = "../a.py"
    if case == "expired":
        from datetime import UTC, datetime, timedelta
        rig.task = replace(rig.task, delegation_scope=DelegationScope(
            expires_at=datetime.now(UTC)-timedelta(seconds=1)))
        await rig.storage.metadata.save_task(rig.task)
    if case in {"secret", "sensitive", "data", "unclassified"}:
        relative = {"secret": "src/.env", "sensitive": "src/credentials.json",
                    "data": "src/data.db", "unclassified": "src/a.unknown"}[case]
    if case in {"file_deny", "redacted"}:
        policy = Mock()
        policy.decide.return_value = PolicyDecision(
            AccessDecision.DENY if case == "file_deny" else AccessDecision.ALLOW_REDACTED)
    # Secure-open is mocked here; actual pinned OS opening has separate platform tests.
    calls = []
    def backend(root, target, operation, authorize, **kwargs):
        authorize()
        calls.append(target)
        with path.open("rb") as stream:
            return read_fd(stream.fileno(), kwargs["content"])
    monkeypatch.setattr(file_broker, "file_operation", backend)
    if case == "revoked":
        async def revoked(*args, **kwargs):
            raise FileBoundaryError("fixture revocation")
        # Captured bytes aren't published if authoritative postcheck fails.
        old = rig.runtime._project_analysis_request
        count = 0
        async def authority(*args, **kwargs):
            nonlocal count
            count += 1
            return await (old(*args, **kwargs) if count <= 2 else revoked())
        monkeypatch.setattr(rig.runtime, "_project_analysis_request", authority)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            subject = await current_read_subject(
                str(root / (relative if case != "external" else "src/a.py")))
            if case == "forged_actor":
                subject = replace(subject, actor=replace(subject.actor, roles=("admin",)))
            if case == "forged_task":
                subject = replace(subject, task_id="forged")
            if case == "forged_scope":
                subject = replace(subject, scope=DelegationScope())
            result = await FileBroker(policy).read_snapshot(
                SandboxRef(rig.workspace.id, "test", root), relative,
                subject=subject,
                max_bytes=2 if case == "budget" else 100,
                expected_identity=ResourceIdentity(0, 0, 0, 0, 0) if case == "identity" else None)
    if case in {"allow", "nested"}:
        assert result.ok
        assert result.data == b"x = 1\r\n"
        assert result.digest == hashlib.sha256(result.data).hexdigest()
    else:
        assert not result.ok
        assert result.data == b""


@pytest.mark.parametrize("max_bytes", [0, -1, True, 16777217, "10"])
async def test_invalid_budgets_fail_closed(tmp_path, max_bytes):
    result = await FileBroker(PolicyEngine()).read_snapshot(
        SandboxRef("w", "test", tmp_path), "a.py", subject={}, max_bytes=max_bytes)
    assert not result.ok


def test_fd_cancel_and_identity_release_owned_by_caller(tmp_path):
    from Sprout.execution.file_access import FileBoundaryError

    path = tmp_path / "a.py"
    path.write_bytes(b"x")
    stop = Event()
    stop.set()
    with path.open("rb") as stream:
        with pytest.raises(FileBoundaryError, match="snapshot_cancelled"):
            read_fd(stream.fileno(), _ReadOptions(100, None, stop))
    assert stream.closed


def test_fd_growing_file_is_refused(tmp_path, monkeypatch):
    from Sprout.execution.file_access import FileBoundaryError

    path = tmp_path / "a.py"
    path.write_bytes(b"x")
    original = os.read
    def grow(fd, count):
        data = original(fd, count)
        if data:
            with path.open("ab") as writer:
                writer.write(b"extra")
        return data
    monkeypatch.setattr(os, "read", grow)
    with path.open("rb") as stream:
        with pytest.raises(FileBoundaryError):
            read_fd(stream.fileno(), _ReadOptions(3, None, Event()))


async def test_cancel_waits_for_worker_release(rig, monkeypatch):
    entered, released = Event(), Event()
    path = rig.root / "controlled.py"
    path.write_bytes(b"controlled")
    streams = []
    def backend(root, target, operation, authorize, **kwargs):
        try:
            with path.open("rb") as stream:
                streams.append(stream)
                entered.set()
                assert kwargs["content"].cancelled.wait(5)
                from Sprout.execution.file_access import FileBoundaryError
                raise FileBoundaryError("snapshot_cancelled")
        finally:
            released.set()
    monkeypatch.setattr(file_broker, "file_operation", backend)
    async def call():
        with node_scope(rig):
            tool = await issued(rig)
            with keys._project_analysis_call(tool):
                subject = await current_read_subject(str(rig.root / "src/a.py"))
                return await FileBroker(PolicyEngine()).read_snapshot(
                    SandboxRef(rig.workspace.id, "test", rig.root), "src/a.py",
                    subject=subject, max_bytes=100)
    task = asyncio.create_task(call())
    while not entered.is_set() and not task.done():
        await asyncio.sleep(0.001)
    assert entered.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released.is_set()
    assert all(stream.closed for stream in streams)


def test_replacement_before_open_refuses_expected_identity(tmp_path):
    from Sprout.execution.file_access import FileBoundaryError

    path = tmp_path / "a.py"
    path.write_bytes(b"old")
    expected = ResourceIdentity.from_stat(path.stat())
    path.rename(tmp_path / "old.py")
    path.write_bytes(b"new")
    with path.open("rb") as stream:
        with pytest.raises(FileBoundaryError, match="snapshot_identity_changed"):
            read_fd(stream.fileno(), _ReadOptions(100, expected, Event()))


@pytest.mark.parametrize("refusal", ["reparse", "junction", "symlink", "secure_open_unavailable"])
async def test_secure_backend_refusals_have_no_content(rig, monkeypatch, refusal):
    from Sprout.execution.file_access import FileBoundaryError

    backend = Mock(side_effect=FileBoundaryError(refusal))
    monkeypatch.setattr(file_broker, "file_operation", backend)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            subject = await current_read_subject(str(rig.root / "src/a.py"))
            result = await FileBroker(PolicyEngine()).read_snapshot(
                SandboxRef(rig.workspace.id, "test", rig.root), "src/a.py",
                subject=subject, max_bytes=100)
    assert not result.ok and not result.data
    backend.assert_called_once()


async def test_concurrent_principals_are_revalidated_separately(rig, monkeypatch):
    from types import SimpleNamespace

    from Sprout.orchestration.models import ExecutionNode, NodeType

    other_actor = replace(rig.principal, user_id="other")
    other_task = replace(rig.task, id="other-task", actor=other_actor, metadata={})
    await rig.storage.metadata.save_task(other_task)
    pending = ExecutionNode(id="other-node", task_id=other_task.id, type=NodeType.AGENT)
    await rig.storage.metadata.save_execution_node(pending)
    assert await rig.commission(pending, "other-owner", "")
    other_node = await rig.storage.metadata.get_execution_node(pending.id)
    second = SimpleNamespace(**{**vars(rig), "task": other_task, "node": other_node})
    seen = []
    class Policy(PolicyEngine):
        def decide(self, request):
            seen.append((request.task_id, request.actor.user_id))
            return super().decide(request)
    def backend(root, target, operation, authorize, **kwargs):
        authorize()
        return fixture_snapshot()
    monkeypatch.setattr(file_broker, "file_operation", backend)
    broker = FileBroker(Policy())
    async def call(subject):
        with node_scope(subject):
            tool = await issued(subject)
            with keys._project_analysis_call(tool):
                request = await current_read_subject(str(rig.root / "src/a.py"))
                return await broker.read_snapshot(
                    SandboxRef(rig.workspace.id, "test", rig.root), "src/a.py",
                    subject=request, max_bytes=100)
    results = await asyncio.gather(call(rig), call(second))
    assert all(item.ok for item in results)
    assert set(seen) == {(rig.task.id, "owner"), (other_task.id, "other")}


async def test_concurrent_metadata_revocation_discards_capture(rig, monkeypatch):
    entered, resume = Event(), Event()
    def backend(root, target, operation, authorize, **kwargs):
        authorize()
        entered.set()
        assert resume.wait(5)
        return fixture_snapshot(b"must not publish")
    monkeypatch.setattr(file_broker, "file_operation", backend)
    async def call():
        with node_scope(rig):
            tool = await issued(rig)
            with keys._project_analysis_call(tool):
                subject = await current_read_subject(str(rig.root / "src/a.py"))
                return await FileBroker(PolicyEngine()).read_snapshot(
                    SandboxRef(rig.workspace.id, "test", rig.root), "src/a.py",
                    subject=subject, max_bytes=100)
    worker = asyncio.create_task(call())
    try:
        while not entered.is_set() and not worker.done():
            await asyncio.sleep(0.001)
        assert entered.is_set()
        await rig.storage.metadata.save_task(replace(
            rig.task, delegation_scope=DelegationScope(denied_paths=("",))))
    finally:
        resume.set()
    result = await worker
    assert not result.ok and not result.data


def test_chunk_authorization_stops_without_partial_result(tmp_path):
    from Sprout.execution.file_access import FileBoundaryError

    path = tmp_path / "a.py"
    path.write_bytes(b"x" * 131072)
    count = 0
    def authorize():
        nonlocal count
        count += 1
        if count >= 3:
            raise FileBoundaryError("revoked_during_capture")
    with path.open("rb") as stream:
        with pytest.raises(FileBoundaryError, match="revoked_during_capture"):
            read_fd(stream.fileno(), _ReadOptions(200000, None, Event(), authorize))
    assert stream.closed


@pytest.mark.parametrize("case", [
    "missing_digest", "wrong_digest", "no_identity", "wrong_identity_type",
    "wrong_size", "over_budget", "not_bytes", "non_boolean_ok", "error_with_success",
    "negative_device", "zero_inode", "bool_inode", "negative_size", "negative_mtime",
    "string_ctime", "expected_mismatch", "expected_match", "empty", "normal",
])
async def test_backend_result_integrity_without_reread(rig, monkeypatch, case):
    result = fixture_snapshot()
    expected = None
    if case == "missing_digest":
        result = replace(result, digest="")
    elif case == "wrong_digest":
        result = replace(result, digest="0" * 64)
    elif case == "no_identity":
        result = replace(result, identity=None)
    elif case == "wrong_identity_type":
        result = replace(result, identity={"inode": 2})
    elif case == "wrong_size":
        result = replace(result, identity=replace(result.identity, size=8))
    elif case == "over_budget":
        result = fixture_snapshot(b"x" * 101)
    elif case == "not_bytes":
        result = replace(result, data=bytearray(result.data))
    elif case == "non_boolean_ok":
        result = replace(result, ok=1)
    elif case == "error_with_success":
        result = replace(result, reason="failed")
    elif case in {"negative_device", "zero_inode", "bool_inode", "negative_size",
                  "negative_mtime", "string_ctime"}:
        field, value = {
            "negative_device": ("device", -1), "zero_inode": ("inode", 0),
            "bool_inode": ("inode", True), "negative_size": ("size", -1),
            "negative_mtime": ("mtime_ns", -1), "string_ctime": ("ctime_ns", "0"),
        }[case]
        result = replace(result, identity=replace(result.identity, **{field: value}))
    elif case == "expected_mismatch":
        expected = replace(result.identity, inode=3)
    elif case == "expected_match":
        expected = result.identity
    elif case == "empty":
        result = fixture_snapshot(b"")
    backend = Mock(return_value=result)
    monkeypatch.setattr(file_broker, "file_operation", backend)
    # No file is created; a second open/read cannot satisfy the empty-file positive.
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            subject = await current_read_subject(str(rig.root / "src/absent.py"))
            output = await FileBroker(PolicyEngine()).read_snapshot(
                SandboxRef(rig.workspace.id, "test", rig.root), "src/absent.py",
                subject=subject, max_bytes=100, expected_identity=expected)
    backend.assert_called_once()
    if case in {"empty", "normal", "expected_match"}:
        assert output.ok and output.data == result.data
    else:
        assert output.ok is False
        assert output.data == b"" and output.digest == "" and output.identity is None


@pytest.mark.parametrize("relative,allowed", [
    ("src/nested/allowed.py", True),
    ("src/private/denied.py", False),
    ("src2/nested/allowed.py", False),
])
async def test_real_nested_fixtures_broker_scope_not_os_open(rig, monkeypatch, relative, allowed):
    paths = ("src/nested/allowed.py", "src/private/denied.py", "src2/nested/allowed.py")
    for name in paths:
        path = rig.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    accessed = []
    def backend(root, target, operation, authorize, **kwargs):
        authorize()  # Scope must reject before actual fixture reading.
        accessed.append(target)
        with target.open("rb") as stream:
            return read_fd(stream.fileno(), kwargs["content"])
    monkeypatch.setattr(file_broker, "file_operation", backend)
    broker = FileBroker(PolicyEngine())
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            subject = await current_read_subject(str(rig.root / relative))
            assert not subject.scope.permits("file.read", str(rig.root))
            listing = await broker.enumerate_readable()
            assert not listing.ok
            output = await broker.read_snapshot(
                SandboxRef(rig.workspace.id, "test", rig.root), relative,
                subject=subject, max_bytes=100)
    assert output.ok is allowed
    assert len(accessed) == (1 if allowed else 0)
    if allowed:
        assert output.data == relative.encode()
