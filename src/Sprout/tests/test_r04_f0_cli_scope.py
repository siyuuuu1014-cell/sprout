"""CLI host selectors (unit boundaries only, not real-model evidence)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from Sprout.cli.commands import project
from Sprout.gateway.identity import Principal
from Sprout.gateway.project_gateway import ProjectGateway


def test_code_cli_passes_finite_scope_and_known_inputs(monkeypatch):
    principal = Principal(user_id="controlled-os-user", source="cli", authenticated=True)
    monkeypatch.setattr(project, "current_cli_principal", lambda: principal)
    create = AsyncMock(return_value=SimpleNamespace(id="created-task"))
    monkeypatch.setattr(project, "_gateway", lambda config: SimpleNamespace(create_task=create))
    result = CliRunner().invoke(project.app, ["task-create", "workspace", "fix function",
        "--mode", "code", "--read-path", "src/app.py", "--read-path", "tests/test_frozen.py",
        "--write-path", "src/app.py", "--allow-pytest"])
    assert result.exit_code == 0, result.output
    kwargs = create.await_args.kwargs
    assert kwargs["principal"] is principal
    assert kwargs["preparation_paths"] == ("src/app.py", "tests/test_frozen.py")
    scope = kwargs["delegation_scope"]
    assert set(scope.allowed_paths) == {"src/app.py", "tests/test_frozen.py"}
    assert scope.allowed_actions == {"tool.invoke", "file.read", "file.write", "process.run"}
    assert not scope.permits("file.read", "../outside.py")


@pytest.mark.parametrize("arguments", [
    ["--read-path", "../outside.py"], ["--read-path", "C:/outside.py"],
    ["--write-path", "src/app.py"], ["--allow-pytest"],
])
def test_invalid_selectors_never_create_task(monkeypatch, arguments):
    gateway = AsyncMock(side_effect=AssertionError("gateway reached"))
    monkeypatch.setattr(project, "_gateway", gateway)
    result = CliRunner().invoke(project.app, ["task-create", "workspace", "fix", *arguments])
    assert result.exit_code != 0
    gateway.assert_not_called()


async def test_gateway_keeps_explicit_preparation_selection_and_principal(tmp_path, monkeypatch):
    from Sprout.gateway.local_identity import current_cli_principal
    from Sprout.task.models import DelegationScope
    from Sprout.tests.fake_model import FakeModel
    from Sprout.tests.test_task_e2e import _repo, _runtime

    root = _repo(tmp_path)
    (root / "src/app.py").write_text("value = 1\n", encoding="utf-8")
    runtime = _runtime(tmp_path, FakeModel())
    principal = current_cli_principal()
    planned = AsyncMock(wraps=runtime.create_task_planned)
    monkeypatch.setattr(runtime, "create_task_planned", planned)
    try:
        workspace = await runtime.open_workspace(root)
        task = await ProjectGateway(runtime, transport="cli", default_user="display").create_task(
            workspace.id, "fix", principal=principal, preparation_paths=("src/app.py",),
            delegation_scope=DelegationScope(
                allowed_paths=(str(root / "src/app.py"),),
                allowed_actions=frozenset({"tool.invoke", "file.read"})))
        kwargs = planned.await_args.kwargs
        assert kwargs["actor"] is principal and kwargs["source"] == principal.source
        assert kwargs["preparation_paths"] == ("src/app.py",)
        assert task.actor == principal
        session = await runtime._sessions.require_owner(task.metadata["session_id"], principal)
        assert session.user_id == principal.user_id
    finally:
        await runtime.stop()
