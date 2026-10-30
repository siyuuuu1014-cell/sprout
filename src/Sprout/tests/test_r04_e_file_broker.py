"""Broker adapter tests, not commissioned Apply E2E."""
import hashlib
import os
from threading import Event
from unittest.mock import Mock

import pytest

from Sprout.execution import file_access
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.file_snapshot import _ReadOptions
from Sprout.gateway.identity import Principal
from Sprout.security.access import AccessDecision, ActionRequest, ActionType, PolicyDecision
from Sprout.security.engine import PolicyEngine
from Sprout.task.models import DelegationScope
from Sprout.workspace.classifier import classify
from Sprout.workspace.models import ResourceRef, Workspace


async def test_missing_host_coordinator_rejects(tmp_path):
    broker = FileBroker(PolicyEngine())
    with pytest.raises(PermissionError):
        await broker._conditional_apply_write(object())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("case", ["success", "scope", "principal", "policy", "version"])
async def test_windows_broker_condition(tmp_path, case):
    if os.name != "nt":
        pytest.skip("Windows native adapter")
    target = tmp_path / "app.py"
    target.write_bytes(b"old\n")
    workspace = Workspace(id="new-workspace", root=tmp_path)
    scope = DelegationScope(allowed_paths=(target.as_posix(),),
                            allowed_actions=frozenset({"file.write"}))
    if case == "scope":
        scope = DelegationScope(denied_actions=frozenset({"file.write"}))
    actor = Principal(user_id="fixture-owner", source="cli", authenticated=case != "principal")
    request = ActionRequest(task_id="new-task", actor=actor, action=ActionType.FILE_WRITE,
        resource=ResourceRef(workspace.id, target.as_posix(),
                             classify(target, workspace_root=tmp_path)), scope=scope)
    policy = PolicyEngine()
    policy.decide = Mock(return_value=PolicyDecision(
        AccessDecision.DENY if case == "policy" else AccessDecision.ALLOW))
    broker = FileBroker(policy)
    token = object()
    # Match production: identity comes from the native secure snapshot handle,
    # not a pathname stat taken immediately after fixture creation.
    original = file_access.file_operation(tmp_path, target, "snapshot", lambda: None,
        content=_ReadOptions(1024, None, Event(), lambda: None))
    assert original.ok
    record = (request, workspace, "app.py", original.identity,
              "0" * 64 if case == "version" else hashlib.sha256(b"old\n").hexdigest(), b"new\n")

    async def resolve(permit):
        if permit is not token:
            raise PermissionError("unregistered")
        return record

    # Controlled Host resolver only; no claim this is an actual Runtime receipt.
    broker._apply_resolver = resolve
    if case == "success":
        await broker._conditional_apply_write(token)
        assert target.read_bytes() == b"new\n"
    else:
        with pytest.raises((PermissionError, OSError)):
            await broker._conditional_apply_write(token)
        assert target.read_bytes() == b"old\n"
