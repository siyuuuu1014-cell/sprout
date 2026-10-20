"""Only temporary files; link races happen after the old final path check."""

import asyncio
import os

import pytest

from Sprout.execution.file_broker import FileBroker
from Sprout.execution.models import SandboxRef
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.security.engine import PolicyEngine
from Sprout.task.models import DelegationScope


@pytest.mark.parametrize("scope", [
    DelegationScope(denied_actions=frozenset({"file.read"})),
    DelegationScope(allowed_paths=("/unrelated",)),
])
async def test_exists_obeys_policy_and_scope(tmp_path, scope):
    (tmp_path / "source.py").write_text("data", encoding="utf-8")
    broker = FileBroker(PolicyEngine())
    assert await broker.exists(SandboxRef("s", "test", tmp_path), "source.py", scope=scope) is False


class DeleteAllowed(PolicyEngine):
    def decide(self, request):
        if request.action is ActionType.FILE_DELETE:
            return PolicyDecision(AccessDecision.ALLOW, "temporary-test delete permission")
        return super().decide(request)


def make_link(source, destination):
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(source), str(destination))
    else:
        destination.symlink_to(source, target_is_directory=True)


@pytest.mark.parametrize("operation", ["read_text", "write_text", "exists", "delete"])
async def test_replacing_parent_after_last_check_cannot_access_outside(
    tmp_path, monkeypatch, operation,
):
    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "src").mkdir(parents=True)
    outside.mkdir()
    (root / "src/source.py").write_text("inside", encoding="utf-8")
    victim = outside / "source.py"
    victim.write_text("outside", encoding="utf-8")
    broker = FileBroker(DeleteAllowed())
    original = asyncio.to_thread
    swapped = False

    async def race(func, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            (root / "src").rename(root / "saved-src")
            make_link(outside, root / "src")
        return await original(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", race)
    args = (SandboxRef("s", "test", root), "src/source.py")
    result = await getattr(broker, operation)(
        *args, *(("changed",) if operation == "write_text" else ()),
    )
    assert swapped
    assert victim.read_text(encoding="utf-8") == "outside"
    assert result is False if operation == "exists" else not result.wrote


async def test_delete_preflight_rechecks_parent_after_policy(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "src").mkdir(parents=True)
    outside.mkdir()
    (root / "src/source.py").write_text("inside", encoding="utf-8")
    (outside / "source.py").write_text("outside", encoding="utf-8")

    class SwapPolicy(DeleteAllowed):
        def decide(self, request):
            if (root / "src").is_dir() and not (root / "saved-src").exists():
                (root / "src").rename(root / "saved-src")
                make_link(outside, root / "src")
            return super().decide(request)

    result = await FileBroker(SwapPolicy()).authorize_delete(
        SandboxRef("s", "test", root), "src/source.py",
    )
    assert not result.wrote


async def test_new_approval_requirement_cannot_reuse_an_earlier_allow(tmp_path):
    target = tmp_path / "source.py"
    target.write_text("keep", encoding="utf-8")

    class ChangingPolicy(DeleteAllowed):
        count = 0

        def decide(self, request):
            self.count += 1
            if self.count >= 3:
                return PolicyDecision(AccessDecision.REQUIRE_APPROVAL, "fresh approval needed")
            return super().decide(request)

    result = await FileBroker(ChangingPolicy()).delete(
        SandboxRef("s", "test", tmp_path), "source.py",
    )
    assert not result.wrote
    assert target.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("operation", [
    "read_text", "write_text", "exists", "authorize_delete", "delete",
])
async def test_linked_sandbox_root_is_not_adopted(tmp_path, operation):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.py").write_text("keep", encoding="utf-8")
    root = tmp_path / "root-link"
    make_link(outside, root)
    broker = FileBroker(DeleteAllowed())
    result = await getattr(broker, operation)(
        SandboxRef("s", "test", root), "source.py",
        *(("change",) if operation == "write_text" else ()),
    )
    assert result is False if operation == "exists" else not result.wrote
    assert (outside / "source.py").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("operation", [
    "read_text", "write_text", "exists", "authorize_delete", "delete",
])
async def test_real_positive_and_negative_file_operations(tmp_path, operation):
    root = tmp_path / "sandbox"
    (root / "src").mkdir(parents=True)
    target = root / "src/source.py"
    target.write_text("original", encoding="utf-8")
    broker = FileBroker(DeleteAllowed())
    sandbox = SandboxRef("s", "test", root)
    scope = DelegationScope(allowed_paths=("src",))
    extra = ("changed",) if operation == "write_text" else ()
    denied = await getattr(broker, operation)(sandbox, "other/source.py", *extra, scope=scope)
    assert denied is False if operation == "exists" else not denied.wrote
    assert not (root / "other").exists()
    result = await getattr(broker, operation)(sandbox, "src/source.py", *extra, scope=scope)
    assert result is True if operation == "exists" else result.wrote, getattr(result, "reason", "")
    if operation == "read_text":
        assert result.content == "original"
    elif operation == "write_text":
        assert target.read_text(encoding="utf-8") == "changed"
    elif operation == "delete":
        assert not target.exists()


@pytest.mark.parametrize("operation", ["read_text", "write_text"])
async def test_replace_after_data_handle_open_does_not_redirect_io(
    tmp_path, monkeypatch, operation,
):
    from Sprout.execution import file_access

    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "src").mkdir(parents=True)
    outside.mkdir()
    (root / "src/source.py").write_text("inside", encoding="utf-8")
    (outside / "source.py").write_text("outside", encoding="utf-8")
    original = file_access._stream
    attempts = []

    def replace_after_open(fd, mode, content, newline):
        try:
            (root / "src").rename(root / "saved-src")
        except OSError:
            attempts.append("blocked")
        else:
            attempts.append("moved")
            make_link(outside, root / "src")
        return original(fd, mode, content, newline)

    monkeypatch.setattr(file_access, "_stream", replace_after_open)
    result = await getattr(FileBroker(PolicyEngine()), operation)(
        SandboxRef("s", "test", root), "src/source.py",
        *(("changed",) if operation == "write_text" else ()),
    )
    assert result.wrote, result.reason
    assert attempts == (["blocked"] if os.name == "nt" else ["moved"])
    assert (outside / "source.py").read_text(encoding="utf-8") == "outside"
    if operation == "read_text":
        assert result.content == "inside"


async def test_scope_expiring_between_preflight_and_open_has_no_write_effect(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    scope = DelegationScope(expires_at=datetime.now(UTC) + timedelta(hours=1))
    original = asyncio.to_thread

    async def expire(func, *args, **kwargs):
        object.__setattr__(scope, "expires_at", datetime.now(UTC) - timedelta(hours=1))
        # The broker binds into a new immutable Scope, so expire the actual
        # time source too rather than mutate the caller's copy as authority.
        monkeypatch.setattr(DelegationScope, "is_expired", lambda self, now=None: True)
        return await original(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", expire)
    result = await FileBroker(PolicyEngine()).write_text(
        SandboxRef("s", "test", tmp_path), "new-dir/new.py", "data", scope=scope,
    )
    assert not result.wrote
    assert not (tmp_path / "new-dir").exists()
@pytest.mark.parametrize("platform", ["nt", "posix"])
@pytest.mark.parametrize("revocation", ["policy", "expiry"])
async def test_reauthorize_after_directory_creation_before_next_effect(
    tmp_path, monkeypatch, platform, revocation,
):
    if os.name != platform:
        pytest.skip("native resource-open test requires " + platform)
    from Sprout.execution import file_access

    class RevocablePolicy(PolicyEngine):
        blocked = False

        def decide(self, request):
            if self.blocked:
                return PolicyDecision(AccessDecision.DENY, "revoked by test")
            return super().decide(request)

    policy = RevocablePolicy()
    created = []

    def revoke():
        if revocation == "policy":
            policy.blocked = True
        else:
            monkeypatch.setattr(DelegationScope, "is_expired", lambda self, now=None: True)

    if platform == "nt":
        kernel = file_access._kernel()
        original = kernel.CreateDirectoryW

        def mkdir(path, security):
            result = original(path, security)
            if result:
                created.append(str(path))
                revoke()
            return result

        monkeypatch.setattr(kernel, "CreateDirectoryW", mkdir)
    else:
        original = os.mkdir

        def mkdir(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            created.append(str(path))
            revoke()
            return result

        monkeypatch.setattr(os, "mkdir", mkdir)
    result = await FileBroker(policy).write_text(
        SandboxRef("s", "test", tmp_path), "new/nested/target.py", "changed",
    )
    assert not result.wrote
    assert len(created) == 1
    assert not (tmp_path / "new/nested").exists()
    assert not (tmp_path / "new/nested/target.py").exists()


@pytest.mark.parametrize("platform", ["nt", "posix"])
@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_revoke_after_leaf_inspection_before_data_effect(
    tmp_path, monkeypatch, platform, operation,
):
    if os.name != platform:
        pytest.skip("native resource-open test requires " + platform)
    from Sprout.execution import file_access

    target = tmp_path / "source.py"
    target.write_text("original", encoding="utf-8")
    revoked = False

    def authorize():
        if revoked:
            raise file_access.FileBoundaryError("test_authority_revoked")

    if platform == "nt":
        original = file_access._win_info

        def info(handle):
            nonlocal revoked
            result = original(handle)
            if not result.attributes & 0x10:
                revoked = True
            return result

        monkeypatch.setattr(file_access, "_win_info", info)
    elif operation == "delete":
        original = os.stat

        def info(path, *args, **kwargs):
            nonlocal revoked
            result = original(path, *args, **kwargs)
            if path == "source.py" and kwargs.get("follow_symlinks") is False:
                revoked = True
            return result

        monkeypatch.setattr(os, "stat", info)
    else:
        original = os.fstat

        def info(fd):
            nonlocal revoked
            result = original(fd)
            revoked = True
            return result

        monkeypatch.setattr(os, "fstat", info)
    with pytest.raises(file_access.FileBoundaryError, match="test_authority_revoked"):
        file_access.file_operation(tmp_path, target, operation, authorize, content="changed")
    assert revoked
    assert target.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("platform", ["nt", "posix"])
@pytest.mark.parametrize("case,expected", [
    ("file", True), ("directory", True), ("root", True), ("missing", False),
    ("scope_denied", False), ("policy_denied", False), ("link", False),
    ("outside", False), ("secret", False), ("root_denied", False),
])
async def test_exists_directory_and_root_preserve_authority(tmp_path, platform, case, expected):
    if os.name != platform:
        pytest.skip("native resource-open test requires " + platform)
    root = tmp_path / "sandbox"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src/source.py").write_text("data", encoding="utf-8")
    (root / ".env").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    paths = {"file": "src/source.py", "directory": "src", "root": ".",
             "missing": "absent", "scope_denied": "src", "policy_denied": "src",
             "link": "linked", "outside": "../outside", "secret": ".env",
             "root_denied": "."}
    if case == "link":
        make_link(outside, root / "linked")

    class DeniedPolicy(PolicyEngine):
        def decide(self, request):
            return PolicyDecision(AccessDecision.DENY, "test directory access denied")

    policy = DeniedPolicy() if case == "policy_denied" else PolicyEngine()
    scope = (DelegationScope(allowed_paths=("other",))
             if case in {"scope_denied", "root_denied"} else DelegationScope())
    # Directory names have no default readable classification. This explicit,
    # server-side classification makes them policy-authorized for this test;
    # no production policy is changed and Scope/DENY still take precedence.
    broker = FileBroker(policy, classify_overrides={"src": "source", ".": "public"})
    assert await broker.exists(
        SandboxRef("s", "test", root), paths[case], scope=scope,
    ) is expected


@pytest.mark.parametrize("path", ["src", "."])
@pytest.mark.parametrize("operation", ["read_text", "write_text", "authorize_delete", "delete"])
async def test_directory_exists_does_not_make_it_a_file(tmp_path, path, operation):
    (tmp_path / "src").mkdir()
    broker = FileBroker(DeleteAllowed())
    result = await getattr(broker, operation)(
        SandboxRef("s", "test", tmp_path), path,
        *(("changed",) if operation == "write_text" else ()),
    )
    assert not result.wrote
    assert (tmp_path / "src").is_dir()
    assert tmp_path.is_dir()
