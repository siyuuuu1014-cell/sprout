"""Eval host identity/Scope wiring; no native runner, verifier or Git permission."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.evaluation import runner
from Sprout.evaluation.fixtures import CASES
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b4_restoration import next_node
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.fixture
def identity_task_only(rig, monkeypatch):
    """Isolate identity checks from resource planning (covered by S-CODE tests).

    Eval's broad prospective write Scope is not an existing-file read manifest.
    This double creates a real Task, never pretends analysis/verification passed.
    """
    async def create(workspace_id, instruction, *, actor, source, delegation_scope, **kwargs):
        return await rig.runtime.create_task(
            workspace_id, instruction, actor=actor, source=source,
            delegation_scope=delegation_scope)
    monkeypatch.setattr(rig.runtime, "create_task_planned", create)


async def test_suite_missing_subject_refuses_before_fixture_setup(tmp_path, monkeypatch):
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    setup = Mock(side_effect=AssertionError("native fixture setup reached"))
    monkeypatch.setattr(runner, "_repo", setup)
    with pytest.raises(PermissionError, match="eval_subject_unavailable"):
        await runner.run_suite(tmp_path, case_ids=("C01",))
    setup.assert_not_called()
    assert not list(tmp_path.iterdir())


async def test_suite_missing_scope_refuses_before_fixture_setup(rig, monkeypatch):
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    setup = Mock(side_effect=AssertionError("native fixture setup reached"))
    monkeypatch.setattr(runner, "_repo", setup)
    with pytest.raises(PermissionError, match="eval_scope_unavailable"):
        await runner.run_suite(rig.tmp_path, case_ids=("C01",), principal=rig.principal)
    setup.assert_not_called()


@pytest.mark.parametrize("invalid", ["principal", "scope", "workspace", "outside", "expired",
                                     "workspace_root", "root_scope", "empty", "deny_read",
                                     "deny_tool", "principal_string", "principal_dict",
                                     "missing_source", "missing_user"])
async def test_coding_bad_binding_never_calls_oracle_or_task(rig, monkeypatch, invalid):
    from datetime import UTC, datetime, timedelta

    principal, scope, workspace = rig.principal, rig.task.delegation_scope, rig.workspace
    if invalid == "principal":
        principal = replace(principal, authenticated=False)
    elif invalid == "principal_string":
        principal = "cli"
    elif invalid == "principal_dict":
        principal = {"authenticated": True, "source": "cli", "user_id": "owner"}
    elif invalid == "missing_source":
        principal = replace(principal, source="")
    elif invalid == "missing_user":
        principal = replace(principal, user_id="")
    elif invalid == "scope":
        scope = None
    elif invalid == "workspace":
        workspace = replace(workspace, id="forged-workspace")
    elif invalid == "outside":
        scope = replace(scope, allowed_paths=(str(rig.root.parent),))
    elif invalid == "expired":
        scope = replace(scope, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    elif invalid == "workspace_root":
        workspace = replace(workspace, root=rig.root.parent)
    elif invalid == "root_scope":
        scope = replace(scope, allowed_paths=(str(rig.root),))
    elif invalid == "empty":
        scope = replace(scope, allowed_paths=())
    else:
        scope = replace(scope, denied_actions=frozenset({
            "file.read" if invalid == "deny_read" else "tool.invoke"}))
    oracle = Mock(side_effect=AssertionError("oracle reached"))
    create = AsyncMock(side_effect=AssertionError("task creation reached"))
    monkeypatch.setattr(runner, "_oracle", oracle)
    monkeypatch.setattr(rig.runtime, "create_task_planned", create)
    with pytest.raises(PermissionError):
        await runner._coding(rig.runtime, workspace, CASES[3], rig.tmp_path, None,
                             principal=principal, delegation_scope=scope)
    oracle.assert_not_called()
    create.assert_not_called()


@pytest.mark.parametrize("tamper", [None, "actor", "workspace", "scope", "id", "source"])
async def test_coding_passes_host_identity_to_real_task_and_rechecks_authority(
    rig, identity_task_only, monkeypatch, tamper,
):
    from Sprout.cli.commands.project import _eval_scope_for_workspace

    (rig.root / ".git").mkdir()  # Marker only; no native repository claim.
    scope = _eval_scope_for_workspace(rig.workspace, rig.principal)
    monkeypatch.setattr(runner, "_oracle", Mock(return_value={"exit_code": 1}))
    # Deliberately stop before CODE execution; this is no verification success.
    execute = AsyncMock(side_effect=RuntimeError("controlled execution checkpoint"))
    monkeypatch.setattr(rig.runtime, "execute", execute)
    get_task = rig.runtime.get_task
    seen = []
    async def authority(task_id):
        task = await get_task(task_id)
        seen.append(task)
        if tamper == "actor":
            return replace(task, actor=replace(task.actor, user_id="other"))
        if tamper == "workspace":
            return replace(task, workspace_id="other")
        if tamper == "scope":
            return replace(task, delegation_scope=replace(scope, allowed_paths=(str(rig.root),)))
        if tamper == "id":
            return replace(task, id="other-task")
        if tamper == "source":
            return replace(task, source="forged-source")
        return task
    monkeypatch.setattr(rig.runtime, "get_task", authority)
    expected = PermissionError if tamper else RuntimeError
    reason = "eval_task_binding_changed" if tamper else "controlled execution checkpoint"
    with pytest.raises(expected, match=reason):
        await runner._coding(rig.runtime, rig.workspace, CASES[3], rig.tmp_path, None,
                             principal=rig.principal, delegation_scope=scope)
    assert seen and seen[0].actor == rig.principal
    assert seen[0].source == rig.principal.source
    assert seen[0].delegation_scope == scope.bind_paths(str(rig.root))
    if tamper:
        execute.assert_not_called()
    else:
        execute.assert_awaited_once()


async def test_scope_provider_is_host_only_and_invalid_scope_precedes_git_setup(
    rig, monkeypatch,
):
    from Sprout.task.models import DelegationScope

    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    monkeypatch.setattr(runner.shutil, "which", lambda name: "controlled-capability")
    setup = Mock(side_effect=AssertionError("fixture Git setup reached"))
    monkeypatch.setattr(runner, "_repo", setup)
    calls = []
    def provider(workspace, principal):
        calls.append((workspace, principal))
        return DelegationScope()
    report = await runner.run_suite(rig.tmp_path, case_ids=("C01",), principal=rig.principal,
                                    scope_provider=provider)
    assert calls and calls[0][1] is rig.principal
    assert report["scenario_passed"] == 0
    assert "eval_scope_unavailable" in report["results"][0]["error"]
    setup.assert_not_called()


def test_cli_scope_is_fixed_host_policy_not_root_or_case_content(rig):
    from Sprout.cli.commands.project import _eval_scope_for_workspace

    scope = _eval_scope_for_workspace(rig.workspace, rig.principal)
    assert str(rig.root) not in scope.allowed_paths
    assert str(rig.root / "src/app.py") in scope.allowed_paths
    assert str(rig.root / "model-selected.py") not in scope.allowed_paths
    assert not scope.permits("network.post", str(rig.root / "src/app.py"))
    # PROCESS_RUN in the action set cannot silently grant a root cwd/resource.
    assert not scope.permits("process.run", str(rig.root))


def test_e2e_fixture_uses_actual_cli_principal_without_changing_it(rig, monkeypatch):
    import asyncio

    from Sprout.tests.test_task_e2e import _create_host_task

    monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal",
                        lambda: rig.principal)
    create = AsyncMock(return_value=rig.task)
    gateway = Mock(create_task=create)
    asyncio.run(_create_host_task(gateway, rig.workspace, "controlled instruction"))
    assert create.await_args.kwargs["principal"] is rig.principal
    scope = create.await_args.kwargs["delegation_scope"]
    assert str(rig.root) not in scope.allowed_paths
    assert str(rig.root / "src/export.py") in scope.allowed_paths


async def test_eval_created_task_subject_can_reach_real_broker_read(
    rig, captured, identity_task_only, monkeypatch,
):
    from Sprout.cli.commands.project import _eval_scope_for_workspace
    from Sprout.orchestration.models import NodeType
    from Sprout.task.models import TaskStatus
    from Sprout.workspace.models import ReadPlan, ResourceRef

    (rig.root / ".git").mkdir()
    (rig.root / "src/app.py").write_text("def clamp(value):\n    return value\n", encoding="utf-8")
    scope = _eval_scope_for_workspace(rig.workspace, rig.principal)
    monkeypatch.setattr(runner, "_oracle", Mock(return_value={"exit_code": 1}))
    async def first_read_only(task):
        rig.task = replace(task, status=TaskStatus.RUNNING)
        await rig.storage.metadata.save_task(rig.task)
        read = await next_node(rig, NodeType.READ)
        plan = ReadPlan(task_id=task.id, resources=(ResourceRef(rig.workspace.id, "src/app.py"),))
        output = await rig.runtime._executor(rig.storage.metadata).execute_node(
            read, rig.workspace, plan, rig.task)
        assert "def clamp" in repr(output)
        raise RuntimeError("controlled checkpoint after authorized READ")
    monkeypatch.setattr(rig.runtime, "execute", first_read_only)
    with pytest.raises(RuntimeError, match="checkpoint after authorized READ"):
        await runner._coding(rig.runtime, rig.workspace, CASES[3], rig.tmp_path, None,
                             principal=rig.principal, delegation_scope=scope)
    assert captured.opens == ["src/app.py"]


async def test_valid_suite_scope_is_checked_before_native_fixture_setup(rig, monkeypatch):
    from Sprout.cli.commands.project import _eval_scope_for_workspace

    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    monkeypatch.setattr(runner.shutil, "which", lambda name: "controlled-capability")
    order = []
    def provider(workspace, principal):
        assert principal is rig.principal
        order.append("scope")
        return _eval_scope_for_workspace(workspace, principal)
    def blocked_setup(*args):
        order.append("native-setup")
        raise PermissionError("controlled native setup remains unavailable")
    monkeypatch.setattr(runner, "_repo", blocked_setup)
    report = await runner.run_suite(rig.tmp_path, case_ids=("C01",), principal=rig.principal,
                                    scope_provider=provider)
    assert order == ["scope", "native-setup"]
    assert report["scenario_passed"] == 0
    assert "native setup remains unavailable" in report["results"][0]["error"]


async def test_restart_rechecks_subject_before_drive_and_closes_new_runtime(
    rig, identity_task_only, monkeypatch,
):
    from Sprout.cli.commands.project import _eval_scope_for_workspace
    from Sprout.task.models import TaskStatus

    (rig.root / ".git").mkdir()
    scope = _eval_scope_for_workspace(rig.workspace, rig.principal)
    monkeypatch.setattr(runner, "_oracle", Mock(return_value={"exit_code": 1}))
    saved = []
    async def waiting(task):
        # Controlled pre-restart fixture, not an approval or verification grant.
        saved.append(task)
        await rig.storage.metadata.save_task(replace(task, status=TaskStatus.WAITING_APPROVAL))
    monkeypatch.setattr(rig.runtime, "execute", waiting)
    monkeypatch.setattr(rig.runtime, "stop", AsyncMock())
    new_runtime = SimpleNamespace(get_workspace=AsyncMock(return_value=rig.workspace),
                                   stop=AsyncMock())
    async def changed_subject(task_id):
        return replace(saved[0], actor=replace(rig.principal, user_id="different-owner"))
    new_runtime.get_task = changed_subject
    monkeypatch.setattr(runner, "_runtime", lambda *args: new_runtime)
    drive = AsyncMock(side_effect=AssertionError("restart model/approval reached"))
    monkeypatch.setattr(runner, "_drive", drive)
    with pytest.raises(PermissionError, match="eval_task_binding_changed"):
        await runner._coding(rig.runtime, rig.workspace, replace(CASES[3], mode="restart"),
                             rig.tmp_path, None, principal=rig.principal, delegation_scope=scope,
                             preparation_paths=("src/app.py",))
    drive.assert_not_called()
    new_runtime.stop.assert_awaited_once()
