"""Actual Strategy/Broker input selection distinct from future output authority."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.evaluation import runner
from Sprout.evaluation.fixtures import CASES
from Sprout.orchestration.models import ExecutionNode, NodeType
from Sprout.task.models import TaskStatus
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.fixture
def inputs(rig, captured, monkeypatch):
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    (rig.root / ".git").mkdir()
    (rig.root / "src/app.py").write_text("def clamp(value):\n    return value\n")
    (rig.root / "pyproject.toml").write_text("[tool.ruff]\nline-length=100\n")
    return ("src/app.py", "pyproject.toml")


async def test_mixed_scope_and_explicit_inputs_reach_strategy_and_real_read(rig, captured, inputs):
    scope = replace(rig.task.delegation_scope, allowed_paths=tuple(str(rig.root / p) for p in (
        *inputs, "src/future.py", "tests", "examples")))
    task = await rig.runtime.create_task_planned(rig.workspace.id, "fix clamp",
        actor=rig.principal, source=rig.principal.source, delegation_scope=scope,
        preparation_paths=inputs)
    assert task.delegation_scope == scope
    assert tuple(task.metadata["required_paths"]) == inputs
    assert set(captured.opens) == set(inputs)
    await rig.storage.metadata.save_task(replace(task, status=TaskStatus.RUNNING))
    node = ExecutionNode(task_id=task.id, type=NodeType.READ)
    await rig.storage.metadata.save_execution_node(node)
    result = await rig.runtime.execute_node(node)
    assert result["status"] == "completed" and "def clamp" in repr(result)
    assert set(captured.opens) == set(inputs)


@pytest.mark.parametrize("bad", [
    "deny", "expired", "principal", "outside", "workspace",
    "missing_scope", "empty_scope", "source"])
async def test_bad_input_authority_never_falls_back(rig, captured, inputs, bad):
    actor = rig.principal
    scope = replace(rig.task.delegation_scope,
                    allowed_paths=tuple(str(rig.root / p) for p in inputs))
    paths = inputs
    workspace_id = rig.workspace.id
    source = actor.source
    if bad == "deny":
        scope = replace(scope, denied_actions=frozenset({"file.read"}))
    if bad == "expired":
        scope = replace(scope, expires_at=datetime.now(UTC)-timedelta(seconds=1))
    if bad == "principal":
        actor = replace(actor, authenticated=False)
    if bad == "outside":
        paths = (*inputs, "src2/outside.py")
    if bad == "workspace":
        workspace_id = "wrong"
    if bad == "missing_scope":
        scope = None
    if bad == "empty_scope":
        scope = replace(scope, allowed_paths=())
    if bad == "source":
        source = "forged"
    with pytest.raises((PermissionError, LookupError)):
        await rig.runtime.create_task_planned(workspace_id, "change", actor=actor,
            source=source, delegation_scope=scope, preparation_paths=paths)
    assert not captured.opens


async def test_actual_eval_coding_uses_known_inputs_not_future_scope(
    rig, captured, inputs, monkeypatch,
):
    from Sprout.cli.commands.project import _eval_scope_for_workspace

    for path, content in {"src/__init__.py": "", "pytest.ini": "[pytest]\n",
                          "conftest.py": "", ".gitignore": "*.pyc\n",
                          "tests/test_regression.py": "def test_ok():\n    assert True\n"}.items():
        target = rig.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    monkeypatch.setattr(runner, "_oracle", Mock(return_value={"exit_code": 1}))
    execute = AsyncMock(side_effect=RuntimeError("checkpoint before native CODE"))
    monkeypatch.setattr(rig.runtime, "execute", execute)
    scope = _eval_scope_for_workspace(rig.workspace, rig.principal)
    with pytest.raises(RuntimeError, match="checkpoint before native CODE"):
        await runner._coding(rig.runtime, rig.workspace, CASES[3], rig.tmp_path, None,
                             principal=rig.principal, delegation_scope=scope)
    task = execute.await_args.args[0]
    assert task.delegation_scope == scope.bind_paths(str(rig.root))
    assert "src/app.py" in task.metadata["required_paths"]
    assert "src/helper.py" not in captured.opens
    assert "tests" not in captured.opens and "examples" not in captured.opens


@pytest.mark.parametrize("case", [c for c in CASES if c.mode in {"code", "force", "restart"}])
def test_cli_host_known_inputs_do_not_include_future_outputs(rig, case):
    from Sprout.cli.commands.project import _eval_inputs_for_workspace

    paths = _eval_inputs_for_workspace(rig.workspace, rig.principal, case)
    assert "pyproject.toml" in paths and "tests/test_regression.py" in paths
    assert "tests" not in paths and "examples" not in paths
    assert "src/helper.py" not in paths
    assert ("src/app.py" in paths) is (case.id == "C01")


async def test_unknown_case_cannot_supply_inputs_through_case_content(rig, monkeypatch):
    from Sprout.cli.commands.project import _eval_scope_for_workspace

    oracle = Mock(side_effect=AssertionError("oracle reached"))
    monkeypatch.setattr(runner, "_oracle", oracle)
    with pytest.raises(PermissionError, match="eval_preparation_inputs_unavailable"):
        await runner._coding(rig.runtime, rig.workspace,
            replace(CASES[3], initial={"../../outside": "content"}), rig.tmp_path, None,
            principal=rig.principal,
            delegation_scope=_eval_scope_for_workspace(rig.workspace, rig.principal))
    oracle.assert_not_called()


async def test_planner_cannot_replace_trusted_input_selection(rig, captured, inputs, monkeypatch):
    from Sprout.strategy.execution import ExecutionPlanner

    original = ExecutionPlanner.plan_to_task_spec
    def changed(self, plan):
        result = original(self, plan)
        return replace(result, resources=("src/future.py",),
                       metadata={**result.metadata, "required_paths": ["src/future.py"]})
    monkeypatch.setattr(ExecutionPlanner, "plan_to_task_spec", changed)
    scope = replace(rig.task.delegation_scope, allowed_paths=tuple(
        str(rig.root / p) for p in (*inputs, "src/future.py")))
    task = await rig.runtime.create_task_from_strategy(rig.workspace.id, "fix clamp",
        actor=rig.principal, source=rig.principal.source, delegation_scope=scope,
        preparation_paths=inputs)
    assert tuple(task.metadata["required_paths"]) == inputs
    assert set(captured.opens) == set(inputs)


async def test_readonly_selector_cannot_create_default_unrestricted_scope(rig, captured, inputs):
    before = await rig.storage.metadata.list_tasks()
    with pytest.raises(PermissionError, match="preparation_subject_unavailable"):
        await rig.runtime.create_task_planned(rig.workspace.id, "read selected",
            actor=rig.principal, source=rig.principal.source, execution_mode="read_only",
            preparation_paths=inputs)
    assert await rig.storage.metadata.list_tasks() == before
    assert not captured.opens
