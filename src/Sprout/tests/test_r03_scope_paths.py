"""Lexical capability algebra, independent of the host's path flavour."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from Sprout.task.models import DelegationScope


@pytest.mark.parametrize("grant,path,expected", [
    ("/repo", "/repo-backup/a", False), ("/repo", "/repo/a", True),
    ("/repo", "/repo", True), ("/repo/", "/repo/a", True),
    ("/repo/file.py", "/repo/file.py.bak", False),
    ("/repo/file.py", "/repo/file.py", True), ("/repo", "/repo/../other", False),
    ("/repo", "/repo/./sub/../a", True), ("/Repo", "/repo/a", False),
    ("/", "/repo/a", True), ("/", "repo/a", False),
    ("C:/Repo", "c:\\repo\\A.py", True), ("C:/Repo", "D:/Repo/a", False),
    ("C:/Repo", "C:/Repo-backup/a", False), ("C:/Repo", "C:Repo/a", False),
    ("//server/share/Repo", "\\\\SERVER\\SHARE\\repo\\a", True),
    ("//server/share/Repo", "//server/share/Repo2/a", False),
    ("//server/share", "//server/other/a", False),
    ("C:/Repo", "\\\\?\\C:\\Repo\\a", False),
    ("C:/Repo", "\\\\.\\C:\\Repo\\a", False),
    ("C:/Repo", "C:/Repo/file.py:secret", False),
    ("C:/Repo", "C:/Repo/NUL.txt", False),
    ("C:/Repo", "C:/Repo/../Repo/a", True),
    ("src", "src/app.py", True), ("src", "src-backup/app.py", False),
    ("src", "src/../../escape", False), ("src", "/src/app.py", False),
    ("", "/anything", False), ("/repo", "", False),
])
def test_component_boundary_and_path_flavour(grant, path, expected):
    assert DelegationScope(allowed_paths=(grant,)).permits("file.read", path) is expected


def test_disjoint_action_intersection_is_deny_all_and_survives_old_shape():
    left = DelegationScope(allowed_actions=frozenset({"file.read"}))
    right = DelegationScope(allowed_actions=frozenset({"file.write"}))
    result = left.narrow(right)
    restored = DelegationScope(**asdict(result))
    for action in ("file.read", "file.write", "tool.invoke", "network.get"):
        assert not result.permits(action, "/repo/a")
        assert not restored.permits(action)


@pytest.mark.parametrize("left,right", [
    (("/repo",), ("/repo-backup",)), (("C:/Repo",), ("C:/Repo2",)),
    (("//host/share/a",), ("//host/share/ab",)),
])
def test_disjoint_paths_cannot_become_a_shared_subtree(left, right):
    scope = DelegationScope(allowed_paths=left).narrow(DelegationScope(allowed_paths=right))
    assert not scope.permits("file.read", right[0] + "/x")


def test_empty_collections_remain_unrestricted_but_denied_wins():
    assert DelegationScope().permits("file.read", "/anywhere")
    assert DelegationScope().permits("tool.invoke")
    assert not DelegationScope(denied_paths=("",)).permits("tool.invoke")
    scope = DelegationScope(allowed_paths=("/repo",), denied_paths=("/repo/private",))
    assert not scope.permits("file.read", "/repo/private/x")
    assert scope.permits("file.read", "/repo/private-backup/x")


def test_expiry_and_depth_cannot_be_relaxed():
    expired = DelegationScope(expires_at=datetime.now(UTC) - timedelta(seconds=1), max_depth=1)
    result = expired.narrow(DelegationScope(max_depth=99))
    assert not result.permits("file.read", "/repo/a")
    assert result.max_depth == 0


@pytest.mark.parametrize("bad", ["", "C:repo", "\\repo", "//server", "C:/repo/a.",
                                 "C:/repo/a ", "/repo/\0x", "//server/share./a"])
def test_unproved_paths_fail_closed_even_when_mixed_with_valid_grants(bad):
    scope = DelegationScope(allowed_paths=("/repo", bad))
    assert not scope.permits("file.read", "/repo/a")


def test_relative_scope_binding_uses_only_the_explicit_absolute_base():
    grant = DelegationScope(allowed_paths=("src/./",), denied_paths=("src/private",))
    bound = grant.bind_paths("C:/sandbox")
    assert bound.permits("file.read", "c:/SANDBOX/src/app.py")
    assert not bound.permits("file.read", "c:/sandbox/src/private/a")
    assert not bound.permits("file.read", "c:/sandbox/src-old/a")
    assert not grant.permits("file.read", "c:/sandbox/src/app.py")
    assert not grant.bind_paths("relative-root").permits("file.read", "src/app.py")


def test_naive_expiry_and_invalid_depth_are_not_delegable():
    assert not DelegationScope(expires_at=datetime(2020, 1, 1)).permits("file.read", "/repo/a")
    assert not DelegationScope(max_depth=-1).can_delegate()
    assert DelegationScope(max_depth=2).narrow(DelegationScope(max_depth=-3)).max_depth == 0


@pytest.mark.parametrize("record,allowed", [
    ({"allowed_actions": ["file.read"], "allowed_paths": ["/repo"]}, True),
    ({"allowed_actions": [], "allowed_paths": [], "denied_paths": [""]}, False),
    ({"version": 99}, False), ({"version": 1}, False),
    ({"allowed_paths": ""}, False), ({"denied_paths": ""}, False),
    ({"allowed_actions": ""}, False), ([], False),
])
async def test_legacy_scope_json_and_invalid_records(record, allowed):
    import json

    from Sprout.storage.local.sqlite.driver import SqliteDatabase
    from Sprout.storage.local.sqlite.metadata import SqliteMetadataStore
    from Sprout.task.models import Task

    database = SqliteDatabase(":memory:")
    try:
        store = SqliteMetadataStore(database)
        task = Task()
        await store.save_task(task)
        await database.execute("UPDATE tasks SET delegation_scope_json=? WHERE id=?",
                               (json.dumps(record), task.id))
        restored = await store.get_task(task.id)
        assert restored.delegation_scope.permits("file.read", "/repo/a") is allowed
        assert not restored.delegation_scope.can_delegate()
    finally:
        database.close()


def test_narrow_never_broadens_component_or_action_domains():
    scopes = [DelegationScope(), DelegationScope(allowed_paths=("/repo",)),
              DelegationScope(allowed_paths=("/repo2",)),
              DelegationScope(allowed_paths=("c:/repo",)),
              DelegationScope(allowed_actions=frozenset({"file.read"})),
              DelegationScope(allowed_actions=frozenset({"file.write"}))]
    for left in scopes:
        for right in scopes:
            narrowed = left.narrow(right)
            for path in ("/repo/a", "/repo2/a", "C:/Repo/a", "relative/a", ""):
                for action in ("file.read", "file.write", "tool.invoke"):
                    assert narrowed.permits(action, path) == (
                        left.permits(action, path) and right.permits(action, path)
                    )


async def test_sqlite_task_roundtrip_preserves_expiry_and_depth():
    from Sprout.storage.local.sqlite.driver import SqliteDatabase
    from Sprout.storage.local.sqlite.metadata import SqliteMetadataStore
    from Sprout.task.models import Task

    database = SqliteDatabase(":memory:")
    try:
        store = SqliteMetadataStore(database)
        expiry = datetime.now(UTC) - timedelta(seconds=1)
        task = Task(delegation_scope=DelegationScope(expires_at=expiry, max_depth=2))
        await store.save_task(task)
        restored = await store.get_task(task.id)
        assert restored.delegation_scope.expires_at == expiry
        assert restored.delegation_scope.max_depth == 2
        assert not restored.delegation_scope.permits("file.read", "/repo/a")
    finally:
        database.close()
@pytest.mark.parametrize("invalid", ["C:relative", "", "../outside", "\\\\?\\C:\\repo"])
def test_invalid_allowed_path_cannot_be_removed_by_narrow(invalid):
    parent = DelegationScope(allowed_paths=("/repo", invalid))
    child = DelegationScope(allowed_paths=("/repo/sub",))
    assert not parent.permits("file.read", "/repo/sub/x")
    assert not parent.narrow(child).permits("file.read", "/repo/sub/x")
    assert not child.narrow(parent).permits("file.read", "/repo/sub/x")


def test_narrow_never_grants_more_than_either_operand_with_invalid_scopes():
    from datetime import UTC, datetime, timedelta

    scopes = [
        DelegationScope(),
        DelegationScope(allowed_paths=("/repo",)),
        DelegationScope(allowed_paths=("/repo/sub",)),
        DelegationScope(allowed_paths=("/repo", "C:relative")),
        DelegationScope(denied_paths=("/repo/secret", "C:relative")),
        DelegationScope(allowed_paths=("C:/repo",)),
        DelegationScope(allowed_paths=("src",)),
        DelegationScope(allowed_paths=("",)),
        DelegationScope(denied_paths=("",)),
        DelegationScope(allowed_actions=frozenset({"file.read"}), max_depth=4),
        DelegationScope(allowed_actions=frozenset({"file.write"}), max_depth=2),
        DelegationScope(denied_actions=frozenset({"file.read"})),
        DelegationScope(expires_at=datetime.now(UTC) - timedelta(days=1)),
        DelegationScope(expires_at=datetime(2020, 1, 1)),
    ]
    for left in scopes:
        for right in scopes:
            result = left.narrow(right)
            assert result.max_depth <= min(left.max_depth, right.max_depth)
            for action in ("file.read", "file.write", "process.run"):
                for path in ("", "/repo/sub/x", "/repo/secret/x", "/repo-backup/x",
                             "C:/repo/x", "src/x", "C:relative", "../x"):
                    if result.permits(action, path):
                        assert left.permits(action, path), (left, right, action, path)
                        assert right.permits(action, path), (left, right, action, path)
