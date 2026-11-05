"""Real Metadata/Guard capture; secure opens are controlled temporary-file doubles."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from Sprout.orchestration.models import ExecutionNode, NodeType
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.task.models import TaskExecutionMode, TaskStatus
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.tests.test_r04_workspace_service_authorization import host_task
from Sprout.workspace.scanner import WorkspaceScanner


@pytest.fixture
def preparation(rig, monkeypatch):
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    (rig.root / ".git").mkdir()
    def forbidden(*args, **kwargs):
        raise AssertionError("unrestricted Scanner reached")
    monkeypatch.setattr(WorkspaceScanner, "scan", forbidden)
    return rig.runtime


async def test_code_finite_selection_real_guard_and_no_scanner(rig, captured, preparation):
    task = await host_task(rig, execution_mode=TaskExecutionMode.CODE)
    workspace, plan = await preparation._prepare_code_resources(task, rig.workspace)
    assert {r.path for r in plan.resources} == {"src/main.py", "src/worker.py"}
    assert plan.task_id == task.id
    assert set(captured.opens) == {"src/main.py", "src/worker.py"}
    assert workspace.manifest is not None
    assert not preparation._preparation_tasks
    tasks = await rig.storage.metadata.list_tasks()
    children = [t for t in tasks if t.metadata.get("plan_kind") == "r04_preparation_v1"]
    assert len(children) == 1
    assert children[0].delegation_scope.is_expired()
    nodes = await rig.storage.metadata.list_execution_nodes(children[0].id)
    assert nodes and all(n.metadata.get("execution_guard", {}).get("owner") for n in nodes)


@pytest.mark.parametrize("case", ["file_deny", "expired", "wrong_actor", "outside", "directory"])
async def test_code_refusal_before_open(rig, captured, preparation, case):
    paths = ("src/private/denied.py",) if case == "file_deny" else ("src/main.py",)
    task = await host_task(rig, paths, execution_mode=TaskExecutionMode.CODE)
    if case == "expired":
        task = replace(task, delegation_scope=replace(task.delegation_scope,
                       expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    if case == "outside":
        task = replace(task, metadata={"required_paths": ["src2/outside.py"]})
    if case == "directory":
        task = replace(task, metadata={"analysis_directory": "src"})
    await rig.storage.metadata.save_task(task)
    if case == "wrong_actor":
        task = replace(task, actor=replace(task.actor, user_id="other"))
    with pytest.raises(PermissionError):
        await preparation._prepare_code_resources(task, rig.workspace)
    assert not captured.opens
    assert not preparation._preparation_tasks


async def test_parent_task_specific_policy_cannot_be_lost(rig, captured, preparation, monkeypatch):
    task = await host_task(rig, execution_mode=TaskExecutionMode.CODE)
    policy = preparation._policy_engine
    original = policy.decide
    seen = []
    def decide(request):
        seen.append(request)
        if request.task_id == task.id and request.action is ActionType.FILE_READ:
            return PolicyDecision(AccessDecision.DENY, "parent-specific deny")
        return original(request)
    monkeypatch.setattr(policy, "decide", decide)
    with pytest.raises(PermissionError):
        await preparation._prepare_code_resources(task, rig.workspace)
    assert any(r.task_id == task.id and r.action is ActionType.FILE_READ for r in seen)
    assert not captured.opens


async def test_scope_change_during_capture_prevents_publication(
    rig, captured, preparation, monkeypatch,
):
    task = await host_task(rig, execution_mode=TaskExecutionMode.CODE)
    analyze = preparation._workspaces.analyze_view
    async def revoke(*args, **kwargs):
        result = await analyze(*args, **kwargs)
        await rig.storage.metadata.save_task(replace(task, delegation_scope=replace(
            task.delegation_scope, denied_actions=frozenset({"file.read"}))))
        return result
    monkeypatch.setattr(preparation._workspaces, "analyze_view", revoke)
    with pytest.raises(PermissionError):
        await preparation._prepare_code_resources(task, rig.workspace)
    assert not preparation._preparation_tasks


async def test_strategy_uses_authorized_view_before_creating_code_task(
    rig, captured, preparation,
):
    scope = replace(rig.task.delegation_scope,
                    allowed_paths=(str(rig.root / "src/main.py"),
                                   str(rig.root / "src/worker.py")))
    task = await preparation.create_task_from_strategy(
        rig.workspace.id, "change main and worker", actor=rig.principal,
        source=rig.principal.source, delegation_scope=scope)
    assert task.execution_mode is TaskExecutionMode.CODE
    assert task.actor == rig.principal and task.delegation_scope == scope
    assert set(captured.opens) == {"src/main.py", "src/worker.py"}
    assert not preparation._preparation_tasks


@pytest.mark.parametrize("case", ["missing", "unauthenticated", "source", "scope", "denied"])
async def test_strategy_no_unauthorized_fallback(rig, captured, preparation, case):
    actor = rig.principal
    scope = replace(rig.task.delegation_scope,
                    allowed_paths=(str(rig.root / "src/main.py"),))
    source = actor.source
    if case == "missing":
        actor = None
    if case == "unauthenticated":
        actor = replace(actor, authenticated=False)
    if case == "source":
        source = "forged"
    if case == "scope":
        scope = replace(scope, allowed_paths=())
    if case == "denied":
        scope = replace(scope, denied_actions=frozenset({"file.read"}))
    with pytest.raises(PermissionError):
        await preparation.create_task_planned(
            rig.workspace.id, "change main", actor=actor, source=source,
            delegation_scope=scope)
    assert not captured.opens
    assert not preparation._preparation_tasks


async def test_actual_code_read_node_returns_only_authorized_content(rig, captured, preparation):
    task = await host_task(rig, ("src/main.py",), execution_mode=TaskExecutionMode.CODE,
                           status=TaskStatus.RUNNING)
    node = ExecutionNode(id="code-read", task_id=task.id, type=NodeType.READ)
    await rig.storage.metadata.save_execution_node(node)
    result = await preparation.execute_node(node)
    assert result["status"] == "completed", result
    assert "def main" in repr(result)
    assert set(captured.opens) == {"src/main.py"}


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_capture_failure_revokes_temporary_task_without_scanner_fallback(
    rig, captured, preparation, monkeypatch, failure,
):
    task = await host_task(rig, execution_mode=TaskExecutionMode.CODE)
    async def interrupted(*args, **kwargs):
        raise failure("controlled interruption")
    monkeypatch.setattr(preparation._workspaces, "analyze_view", interrupted)
    with pytest.raises((PermissionError, RuntimeError, asyncio.CancelledError)):
        await preparation._prepare_code_resources(task, rig.workspace)
    assert not preparation._preparation_tasks
    children = [t for t in await rig.storage.metadata.list_tasks()
                if t.metadata.get("plan_kind") == "r04_preparation_v1"]
    assert children and all(t.delegation_scope.is_expired() for t in children)
    assert (await rig.storage.metadata.get_task(task.id)).delegation_scope == task.delegation_scope


async def test_unknown_owner_is_not_reclaimed_for_preparation(rig, captured, preparation):
    task = await host_task(rig, execution_mode=TaskExecutionMode.CODE)
    node = ExecutionNode(id="active-code", task_id=task.id, type=NodeType.AGENT)
    await rig.storage.metadata.save_execution_node(node)
    assert await rig.commission(node, "original-owner", "")
    before = await rig.storage.metadata.get_execution_node(node.id)
    with pytest.raises(PermissionError, match="code_preparation_unconfirmed"):
        await preparation._prepare_code_resources(task, rig.workspace)
    assert await rig.storage.metadata.get_execution_node(node.id) == before
    assert not captured.opens


async def test_stale_workspace_manifest_is_not_a_read_authority(rig, captured, preparation):
    task = await host_task(rig, ("src/main.py",), execution_mode=TaskExecutionMode.CODE)
    # Deliberately unusable legacy manifest: it must not be inspected or reused.
    workspace, plan = await preparation._prepare_code_resources(
        task, replace(rig.workspace, manifest=object()))
    assert workspace.manifest is not None
    assert [r.path for r in plan.resources] == ["src/main.py"]
    assert set(captured.opens) == {"src/main.py"}


async def test_persisted_marker_without_live_issuance_cannot_publish(
    rig, captured, preparation, monkeypatch,
):
    task = await host_task(rig, execution_mode=TaskExecutionMode.CODE)
    analyze = preparation._workspaces.analyze_view
    async def lose_live_registration(*args, **kwargs):
        result = await analyze(*args, **kwargs)
        preparation._preparation_tasks.clear()
        return result
    monkeypatch.setattr(preparation._workspaces, "analyze_view", lose_live_registration)
    with pytest.raises(PermissionError):
        await preparation._prepare_code_resources(task, rig.workspace)
    assert not preparation._preparation_tasks


async def test_strategy_reads_conventions_only_from_captured_config(rig, captured, preparation):
    (rig.root / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    scope = replace(rig.task.delegation_scope, allowed_paths=tuple(
        str(rig.root / path) for path in ("src/main.py", "pyproject.toml")))
    task = await preparation.create_task_from_strategy(
        rig.workspace.id, "change main", actor=rig.principal, source=rig.principal.source,
        delegation_scope=scope)
    assert task is not None
    assert set(captured.opens) == {"src/main.py", "pyproject.toml"}


@pytest.mark.parametrize("revoke", [False, True])
async def test_model_refinement_stays_in_owned_frame_and_rechecks_scope(
    rig, captured, preparation, monkeypatch, revoke,
):
    from Sprout.strategy.model_planner import ModelStrategyPlanner
    from Sprout.tools.context_keys import _current_project_node

    scope = replace(rig.task.delegation_scope,
                    allowed_paths=(str(rig.root / "src/main.py"),))
    calls = []
    async def refine(self, plan, model):
        frame = _current_project_node()
        assert frame and frame.runtime is preparation
        calls.append(frame.task.id)
        if revoke:
            task = await rig.storage.metadata.get_task(frame.task.id)
            await rig.storage.metadata.save_task(replace(task, delegation_scope=replace(
                task.delegation_scope, denied_actions=frozenset({"file.read"}))))
        return plan
    monkeypatch.setattr(ModelStrategyPlanner, "refine", refine)
    async def create():
        return await preparation.create_task_from_strategy(
            rig.workspace.id, "change main", actor=rig.principal,
            source=rig.principal.source, delegation_scope=scope, use_model_planner=True)
    if revoke:
        with pytest.raises(PermissionError):
            await create()
    else:
        assert await create()
    assert len(calls) == 1 and not preparation._preparation_tasks
