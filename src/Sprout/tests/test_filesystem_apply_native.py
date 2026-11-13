"""Native no-overwrite contract for the FileBroker Apply adapter."""

import hashlib
import os

import pytest

from Sprout.execution.file_access import ConditionalWriteRefused, file_operation

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native conditional creation")


def test_conditional_create_never_overwrites_and_verifies_bytes(tmp_path):
    target = tmp_path / "new.py"
    data = b"print('new')\n"

    def authorize():
        pass

    result = file_operation(tmp_path, target, "conditional_create", authorize,
                            content=data)
    assert result == {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    assert target.read_bytes() == data

    with pytest.raises(ConditionalWriteRefused, match="conditional_create_target_exists"):
        file_operation(tmp_path, target, "conditional_create", authorize, content=b"replacement")
    assert target.read_bytes() == data


def test_conditional_create_does_not_create_parents_or_write_on_denial(tmp_path):
    def deny():
        raise PermissionError("fixture authorization denied")

    target = tmp_path / "new.py"
    with pytest.raises(PermissionError, match="fixture authorization denied"):
        file_operation(tmp_path, target, "conditional_create", deny, content=b"new")
    assert not target.exists()

    with pytest.raises(ConditionalWriteRefused, match="conditional_create_parent_missing"):
        file_operation(tmp_path, tmp_path / "missing" / "new.py", "conditional_create",
                       lambda: None, content=b"new")
    assert not (tmp_path / "missing").exists()

@pytest.mark.parametrize("git", [False, True])
async def test_file_broker_create_requires_exact_apply_permit_and_scope(tmp_path, git):
    from dataclasses import replace

    from Sprout.execution.file_broker import FileBroker
    from Sprout.gateway.identity import Principal
    from Sprout.security.access import ActionRequest, ActionType
    from Sprout.security.engine import PolicyEngine
    from Sprout.task.models import DelegationScope
    from Sprout.workspace.classifier import classify
    from Sprout.workspace.models import ResourceRef, Workspace, WorkspaceKind

    kind = WorkspaceKind.GIT_REPOSITORY if git else WorkspaceKind.LOCAL_DIRECTORY
    workspace = Workspace(id="test", root=tmp_path, kind=kind)
    path = "test_app.py"
    target = tmp_path / path
    request = ActionRequest(
        task_id="task", actor=Principal(user_id="test", source="cli", authenticated=True),
        action=ActionType.FILE_APPLY,
        resource=ResourceRef(workspace.id, target.as_posix(), kind=classify(
            target, workspace_root=tmp_path)),
        scope=DelegationScope(allowed_paths=(target.as_posix(),),
                              allowed_actions=frozenset({"file.apply", "file.write"})),
    )
    broker = FileBroker(PolicyEngine())
    token = object()
    data = b"assert True\n"

    async def resolve(permit):
        if permit is not token:
            raise PermissionError("unrecognized permit")
        return request, workspace, path, None, None, data

    broker._apply_resolver = resolve
    with pytest.raises(PermissionError, match="unrecognized permit"):
        await broker._conditional_apply_write(object())
    allowed = request
    request = replace(request, scope=replace(request.scope, allowed_paths=(
        (tmp_path / "other.py").as_posix(),)))
    with pytest.raises(PermissionError, match="conditional_apply_policy_denied"):
        await broker._conditional_apply_write(token)
    assert not target.exists()

    request = allowed
    result = await broker._conditional_apply_write(token)
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert target.read_bytes() == data

    target.write_bytes(b"external")
    with pytest.raises(ConditionalWriteRefused, match="conditional_create_target_exists"):
        await broker._conditional_apply_write(token)
    assert target.read_bytes() == b"external"

def test_conditional_create_failure_after_open_keeps_path_occupied(tmp_path, monkeypatch):
    from Sprout.execution import file_access

    target = tmp_path / "new.py"

    def fail_after_open(fd, data):
        os.write(fd, data[:2])
        raise OSError("injected write failure")

    with monkeypatch.context() as patch:
        patch.setattr(file_access, "_write_and_verify_fd", fail_after_open)
        with pytest.raises(OSError, match="injected write failure"):
            file_operation(tmp_path, target, "conditional_create", lambda: None,
                           content=b"new content")

    assert target.read_bytes() == b"ne"
    with pytest.raises(ConditionalWriteRefused, match="conditional_create_target_exists"):
        file_operation(tmp_path, target, "conditional_create", lambda: None,
                       content=b"new content")
    assert target.read_bytes() == b"ne"

async def test_git_file_broker_delete_requires_exact_version_and_delete_scope(tmp_path):
    from dataclasses import replace

    from Sprout.execution.file_broker import FileBroker
    from Sprout.execution.file_snapshot import ResourceIdentity
    from Sprout.gateway.identity import Principal
    from Sprout.security.access import ActionRequest, ActionType
    from Sprout.security.engine import PolicyEngine
    from Sprout.task.models import DelegationScope
    from Sprout.workspace.classifier import classify
    from Sprout.workspace.models import ResourceRef, Workspace, WorkspaceKind

    workspace = Workspace(id="test", root=tmp_path, kind=WorkspaceKind.GIT_REPOSITORY)
    target = tmp_path / "app.py"
    target.write_bytes(b"original")
    before = ResourceIdentity.from_stat(target.stat())
    digest = hashlib.sha256(b"original").hexdigest()
    request = ActionRequest(
        task_id="task", actor=Principal(user_id="test", source="cli", authenticated=True),
        action=ActionType.FILE_APPLY,
        resource=ResourceRef(workspace.id, target.as_posix(), kind=classify(
            target, workspace_root=tmp_path)),
        scope=DelegationScope(allowed_paths=(target.as_posix(),),
                              allowed_actions=frozenset({"file.apply", "file.write"})),
    )
    broker = FileBroker(PolicyEngine())
    token = object()

    async def resolve(permit):
        if permit is not token:
            raise PermissionError("unrecognized permit")
        return request, workspace, target.name, before, digest, None

    broker._apply_resolver = resolve
    with pytest.raises(ConditionalWriteRefused, match="conditional_apply_authorization_changed"):
        await broker._conditional_apply_write(token)
    assert target.read_bytes() == b"original"
    request = replace(request, scope=replace(
        request.scope, allowed_actions=frozenset({"file.apply", "file.delete"})))
    digest = hashlib.sha256(b"wrong digest").hexdigest()
    with pytest.raises(ConditionalWriteRefused, match="conditional_write_version_changed"):
        await broker._conditional_apply_write(token)
    assert target.read_bytes() == b"original"
    digest = hashlib.sha256(b"original").hexdigest()
    assert await broker._conditional_apply_write(token) == {"sha256": None, "size": 0}
    assert not target.exists()
