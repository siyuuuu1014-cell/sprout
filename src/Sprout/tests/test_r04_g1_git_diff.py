"""G1 source-contract probes; strict launcher, no actual subprocess."""

from dataclasses import replace
from datetime import UTC
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.git_broker import GitBroker
from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.security.engine import PolicyEngine
from Sprout.task.models import DelegationScope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.workspace.models import ResourceKind, ResourceRef


def test_git_diff_file_permission_does_not_grant_metadata_permission(tmp_path):
    scope = DelegationScope(
        allowed_actions=frozenset({"git.diff", "file.read"}),
        allowed_paths=(str(tmp_path / "src/app.py"),),
    )
    policy = PolicyEngine()
    selected = ActionRequest(
        action=ActionType.GIT_DIFF,
        scope=scope,
        resource=ResourceRef("fixture", str(tmp_path / "src/app.py"), ResourceKind.SOURCE),
    )
    metadata = ActionRequest(
        action=ActionType.FILE_READ,
        scope=scope,
        resource=ResourceRef("fixture", str(tmp_path / ".git/HEAD"), ResourceKind.CONFIG),
    )
    assert policy.decide(selected).decision is AccessDecision.ALLOW
    assert policy.decide(metadata).decision is AccessDecision.DENY
    assert not scope.permits("file.read", str(tmp_path / ".git/index"))


async def test_bound_git_diff_must_attribute_real_task_and_principal(rig, monkeypatch):
    finite = DelegationScope(
        allowed_actions=frozenset({"git.diff", "file.read"}), allowed_paths=("a.py",)
    )
    task = replace(rig.task, delegation_scope=finite)
    await rig.storage.metadata.save_task(task)
    requests = []
    policy = PolicyEngine()
    original = policy.decide

    def decide(request):
        requests.append(request)
        return original(request)

    monkeypatch.setattr(policy, "decide", decide)
    spawn = AsyncMock(side_effect=AssertionError("No native Git in guarded tests"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    with rig.runtime._project_analysis_scope(task, rig.workspace, rig.node) as frame:
        frame.known_paths = ("a.py",)
        await GitBroker(policy).diff(rig.workspace, scope=finite)
    spawn.assert_not_called()
    assert requests and all(r.task_id == task.id and r.actor == task.actor for r in requests)


async def test_selected_file_diff_must_not_authorize_workspace_parent(rig, monkeypatch):
    scope = DelegationScope(allowed_actions=frozenset({"git.diff"}), allowed_paths=("a.py",))
    requests = []
    policy = PolicyEngine()
    original = policy.decide

    def decide(request):
        requests.append(request)
        return original(request)

    monkeypatch.setattr(policy, "decide", decide)
    task = replace(rig.task, delegation_scope=scope)
    await rig.storage.metadata.save_task(task)
    with rig.runtime._project_analysis_scope(task, rig.workspace, rig.node) as frame:
        frame.known_paths = ("a.py",)
        result = await GitBroker(policy).diff(rig.workspace, scope=scope)
    assert not result.allowed
    assert requests and all(r.resource.path != str(rig.workspace.root) for r in requests)


async def test_review_diff_without_current_subject_never_runs_git(rig, monkeypatch):
    spawn = AsyncMock(side_effect=AssertionError("No native Git in guarded tests"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    result = await GitBroker(PolicyEngine()).review_diff(
        rig.workspace, scope=rig.task.delegation_scope
    )
    assert not result.allowed and result.reason == "git_subject_unavailable"
    spawn.assert_not_called()


@pytest.mark.parametrize("change", ["scope", "workspace", "actor", "expired", "path", "secret"])
async def test_invalid_current_git_subject_cannot_spawn(rig, monkeypatch, change):
    from datetime import datetime, timedelta

    scope = DelegationScope(
        allowed_actions=frozenset({"git.diff", "file.read"}), allowed_paths=("a.py", ".env")
    )
    if change == "expired":
        scope = replace(scope, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    task = replace(rig.task, delegation_scope=scope)
    await rig.storage.metadata.save_task(task)
    spawn = AsyncMock(side_effect=AssertionError("Unauthorized Git reached process"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    workspace = replace(rig.workspace, id="different") if change == "workspace" else rig.workspace
    if change == "actor":
        await rig.storage.metadata.save_task(
            replace(task, actor=replace(task.actor, user_id="other"))
        )
    with rig.runtime._project_analysis_scope(task, rig.workspace, rig.node) as frame:
        frame.known_paths = (
            "not_selected.py" if change == "path" else ".env" if change == "secret" else "a.py",
        )
        result = await GitBroker(PolicyEngine()).review_diff(
            workspace, scope=DelegationScope() if change == "scope" else scope
        )
    assert not result.allowed and result.reason != "s1_task_view_unavailable"
    spawn.assert_not_called()
