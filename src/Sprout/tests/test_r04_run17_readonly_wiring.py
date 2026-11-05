"""Run17: scheduling is not reading; finite selections retain broker authority."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.gateway.project_gateway import ProjectGateway
from Sprout.gateway.runtime_gateway import RuntimeGateway
from Sprout.gateway.sdk_gateway import SDKGateway
from Sprout.gateway.task_adapter import GatewayRequest, TaskAdapter
from Sprout.message.models import Message
from Sprout.orchestration.models import NodeType
from Sprout.orchestration.service import SequentialOrchestrator
from Sprout.task.models import TaskStatus
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b3_node_publication import publication as publication
from Sprout.tests.test_r04_b4_restoration import next_node
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


def test_finite_scope_supplies_known_resources_without_scanning(rig):
    task = replace(rig.task, delegation_scope=replace(
        rig.task.delegation_scope, allowed_paths=(str(rig.root / "src/main.py"),)))
    _, plan = rig.runtime._known_read_only_plan(rig.workspace, task)
    assert [resource.path for resource in plan.resources] == ["src/main.py"]
    assert task.metadata == rig.task.metadata


def test_no_selection_is_not_a_successful_project_read(rig):
    task = replace(rig.task, delegation_scope=replace(
        rig.task.delegation_scope, allowed_paths=()))
    with pytest.raises(PermissionError, match="project_auto_discovery_unavailable"):
        rig.runtime._known_read_only_plan(rig.workspace, task)
    _, plan = rig.runtime._known_read_only_plan(rig.workspace, task, scheduling=True)
    assert not plan.resources
    # Scheduling preparation cannot change the later execution requirement.
    with pytest.raises(PermissionError):
        rig.runtime._known_read_only_plan(rig.workspace, task)


def test_explicit_invalid_selection_is_not_replaced_by_scope(rig):
    task = replace(rig.task, metadata={"required_paths": ["../outside.py"]})
    with pytest.raises(PermissionError, match="project_capture_selection_invalid"):
        rig.runtime._known_read_only_plan(rig.workspace, task)


async def test_project_gateway_preserves_explicit_principal(rig, monkeypatch):
    create = AsyncMock(return_value=rig.task)
    monkeypatch.setattr(rig.runtime, "create_task_planned", create)
    gateway = ProjectGateway(rig.runtime, transport="cli", default_user="display-only")
    await gateway.create_task(rig.workspace.id, "explain", principal=rig.principal)
    assert create.await_args.kwargs["actor"] is rig.principal
    assert create.await_args.kwargs["source"] == rig.principal.source


async def test_project_gateway_missing_identity_is_not_authenticated(rig, monkeypatch):
    create = AsyncMock(return_value=rig.task)
    monkeypatch.setattr(rig.runtime, "create_task_planned", create)
    gateway = ProjectGateway(rig.runtime, transport="cli", default_user="display-only")
    await gateway.create_task(rig.workspace.id, "explain")
    assert create.await_args.kwargs["actor"].authenticated is not True


@pytest.mark.parametrize("denied", [False, True])
async def test_scope_selected_read_uses_current_broker_authorization(rig, captured, denied):
    path = "src/private/denied.py" if denied else "src/main.py"
    rig.task = replace(rig.task, delegation_scope=replace(
        rig.task.delegation_scope, allowed_paths=(str(rig.root / path),)))
    await rig.storage.metadata.save_task(rig.task)
    _, plan = rig.runtime._known_read_only_plan(rig.workspace, rig.task)
    read = await next_node(rig, NodeType.READ)
    executor = rig.runtime._executor(rig.storage.metadata)
    if denied:
        with pytest.raises(PermissionError):
            await executor.execute_node(read, rig.workspace, plan, rig.task)
        assert not captured.opens
    else:
        result = await executor.execute_node(read, rig.workspace, plan, rig.task)
        assert "def main" in repr(result)
        assert captured.opens == ["src/main.py"]


@pytest.mark.parametrize("authenticated", [True, False])
async def test_runtime_gateway_finite_scope_reaches_model(
    rig, captured, publication, monkeypatch, authenticated,
):
    _, messages = publication
    rig.runtime._orchestrator_backend = SequentialOrchestrator(rig.storage.metadata)
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    scope = replace(rig.task.delegation_scope,
                    allowed_paths=(str(rig.root / "src/main.py"),))
    response = await RuntimeGateway(rig.runtime, transport="cli").execute(GatewayRequest(
        transport="cli", instruction="explain the selected file",
        caller=replace(rig.principal, authenticated=authenticated),
        workspace_id=rig.workspace.id, delegation_scope=scope, execution_mode="read_only",
    ))
    if not authenticated:
        assert response.status != "completed"
        assert not captured.opens and not messages
        return
    nodes = await rig.storage.metadata.list_execution_nodes(response.task_id)
    assert response.status == "completed", [(n.type, n.metadata) for n in nodes]
    # Publication revalidates captured resources; no other path may be opened.
    assert captured.opens and set(captured.opens) == {"src/main.py"}
    assert "def main" in repr(messages)


@pytest.mark.parametrize("authenticated", [True, False])
def test_adapter_transport_cannot_rewrite_or_authenticate_caller(rig, authenticated):
    principal = replace(rig.principal, source="sdk-host", authenticated=authenticated)
    task = TaskAdapter().to_task(GatewayRequest(
        transport="untrusted-transport-label", instruction="explain", caller=principal,
        metadata={"authenticated": True, "source": "cli", "trusted": True},
    ))
    assert task.actor is principal
    assert task.source == "sdk-host"
    assert task.actor.authenticated is authenticated


def test_message_metadata_cannot_supply_a_verified_principal():
    request = TaskAdapter().from_message(Message(
        "explain", user_id="display-user", channel="cli",
        metadata={"caller": {"authenticated": True}, "authenticated": True,
                  "source": "sdk-host", "roles": ["admin"]},
    ))
    assert request.caller.authenticated is False
    assert not request.caller.roles


@pytest.mark.parametrize("case", ["valid", "unauthenticated", "missing_scope", "denied"])
async def test_sdk_host_subject_selection_and_actual_model_messages(
    rig, captured, publication, monkeypatch, case,
):
    _, messages = publication
    rig.runtime._orchestrator_backend = SequentialOrchestrator(rig.storage.metadata)
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    principal = replace(rig.principal, source="sdk-host", authenticated=case != "unauthenticated")
    path = "src/private/denied.py" if case == "denied" else "src/main.py"
    scope = replace(rig.task.delegation_scope, allowed_paths=(str(rig.root / path),))
    gateway = SDKGateway(rig.runtime, principal=principal)
    if case == "missing_scope":
        with pytest.raises(PermissionError, match="project_auto_discovery_unavailable"):
            await gateway.execute("explain", workspace_id=rig.workspace.id,
                                  execution_mode="read_only")
    else:
        result = await gateway.execute(
            "explain", workspace_id=rig.workspace.id, caller_id="MODEL_IMPOSTOR",
            delegation_scope=scope, execution_mode="read_only")
        task = await rig.storage.metadata.get_task(result.task_id)
        assert task.actor == principal and task.source == principal.source
        if case == "valid":
            assert result.status == "completed"
            assert "def main" in repr(messages)
            assert captured.opens and set(captured.opens) == {"src/main.py"}
            return
        assert result.status != "completed"
    assert not captured.opens and not messages


@pytest.mark.parametrize("authenticated", [True, False])
async def test_project_code_host_first_read_preserves_subject_without_apply(
    rig, captured, monkeypatch, authenticated,
):
    (rig.root / ".git").mkdir()
    principal = replace(rig.principal, authenticated=authenticated)
    scope = replace(rig.task.delegation_scope,
                    allowed_paths=(str(rig.root / "src/main.py"),))
    # Keep this a plain CODE Task; strategy and native Git/Apply are independent.
    monkeypatch.setattr(rig.runtime, "create_task_from_strategy", AsyncMock(
        side_effect=RuntimeError("controlled planner fallback")))
    task = await ProjectGateway(rig.runtime, transport="cli", default_user="display").create_task(
        rig.workspace.id, "explain selected code", principal=principal, delegation_scope=scope)
    assert task.actor == principal and task.delegation_scope == scope
    task = replace(task, status=TaskStatus.RUNNING)
    await rig.storage.metadata.save_task(task)
    rig.task = task
    read = await next_node(rig, NodeType.READ)
    _, plan = rig.runtime._known_read_only_plan(rig.workspace, task)
    executor = rig.runtime._executor(rig.storage.metadata)
    if authenticated:
        result = await executor.execute_node(read, rig.workspace, plan, task)
        assert "def main" in repr(result)
    else:
        with pytest.raises(PermissionError, match="identity unavailable"):
            await executor.execute_node(read, rig.workspace, plan, task)
        assert not captured.opens


@pytest.mark.parametrize("broken_planner", [False, True])
async def test_planned_task_preserves_host_scope_through_strategy_or_fallback(
    rig, captured, monkeypatch, broken_planner,
):
    from Sprout.strategy.execution import ExecutionPlanner
    from Sprout.strategy.pipeline import StrategyPipeline
    from Sprout.task.models import TaskBudget

    (rig.root / ".git").mkdir()
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    scope = replace(rig.task.delegation_scope,
                    allowed_paths=(str(rig.root / "src/main.py"),))
    monkeypatch.setattr(StrategyPipeline, "plan_view", lambda *a, **k: None)
    def task_spec(*args):
        if broken_planner:
            raise RuntimeError("controlled strategy failure")
        return SimpleNamespace(instruction="controlled", resources=("src/main.py",),
                               tools=(), budget=TaskBudget(), metadata={"scope": "forged"})
    monkeypatch.setattr(ExecutionPlanner, "plan_to_task_spec", task_spec)
    task = await rig.runtime.create_task_planned(
        rig.workspace.id, "plan", actor=rig.principal, source=rig.principal.source,
        delegation_scope=scope,
    )
    assert task.actor == rig.principal and task.delegation_scope == scope
