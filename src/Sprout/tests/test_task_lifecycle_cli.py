from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from Sprout.cli.commands import task_lifecycle


@pytest.mark.parametrize("operation,args", [
    ("create", ["workspace", "instruction", "--mode", "read_only"]),
    ("submit", ["task", "--host", "host", "--namespace", "ns", "--task-queue", "queue"]),
    ("report", ["task"]),
    ("observe", ["task", "--submission-id", "record", "--wait-seconds", "1"]),
    ("sync", ["task", "--submission-id", "record"]),
    ("retry-submission", ["task", "--submission-id", "record"]),
])
def test_commands_dispatch_shared_service(monkeypatch, tmp_path, operation, args):
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    operate = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(task_lifecycle, "operate", operate)
    result = CliRunner().invoke(task_lifecycle.app, [operation, *args, "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == '{"ok": true}'
    assert operate.await_args.args[0] == operation.replace("-", "_")
    if operation == "create":
        assert operate.await_args.kwargs["mode"] == "read_only"
    elif operation == "submit":
        assert operate.await_args.kwargs["route"].host == "host"


def test_owner_errors_and_bad_wait_are_normal_cli_errors(monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    operate = AsyncMock(side_effect=PermissionError("Task owner identity does not match"))
    monkeypatch.setattr(task_lifecycle, "operate", operate)
    runner = CliRunner()
    result = runner.invoke(task_lifecycle.app, ["report", "task", "--config", str(config)])
    assert result.exit_code == 2
    assert "owner identity" in result.output
    operate.reset_mock()
    result = runner.invoke(task_lifecycle.app, ["observe", "task", "--wait-seconds", "61",
                                               "--config", str(config)])
    assert result.exit_code == 2
    operate.assert_not_called()


async def test_failed_operation_closes_unstarted_runtime(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from Sprout.gateway.identity import Principal

    runtime = SimpleNamespace(storage=SimpleNamespace(close=AsyncMock()),
        get_task=AsyncMock(return_value=None), start=AsyncMock())
    monkeypatch.setattr("Sprout.runtime.factory.create_runtime", lambda settings: runtime)
    monkeypatch.setattr("Sprout.config.loader.load_settings", lambda config: object())
    monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal", lambda:
        Principal(user_id="owner", authenticated=True, source="cli"))
    with pytest.raises(LookupError):
        await task_lifecycle.operate("report", tmp_path / "config.toml", task_id="missing")
    runtime.storage.close.assert_awaited_once()
    runtime.start.assert_not_called()
