import json
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from Sprout.cli.commands.evolution import app
from Sprout.execution.git_identity import GitIdentityRequired


def inputs(tmp_path):
    replay = tmp_path / "replay.json"
    planned = tmp_path / "git.json"
    replay.write_text("{}", encoding="utf-8")
    planned.write_text("{}", encoding="utf-8")
    return ["commit-identity", "--replay-plan", str(replay), "--git-plan", str(planned),
            "--message", "Explicit change"]


def test_interactive_missing_identity_is_forwarded_without_authorizing(tmp_path, monkeypatch):
    resolve = AsyncMock(side_effect=[GitIdentityRequired(["user.name", "user.email"]),
                                    {"author_name": "Operator", "author_email": "op@example.test"}])
    monkeypatch.setattr("Sprout.execution.git_identity.resolve_commit_contract", resolve)
    result = CliRunner().invoke(app, inputs(tmp_path) + ["--interactive"],
                               input="Operator\nop@example.test\n")
    assert result.exit_code == 0, result.output
    output = json.loads(result.output[result.output.index("{"):])
    assert output["configuration_changed"] is False
    assert output["write_authorized"] is False
    assert output["validation_committed"] is False
    assert resolve.await_args.kwargs["author_name"] == "Operator"
    assert resolve.await_args.kwargs["author_email"] == "op@example.test"


def test_noninteractive_missing_identity_exits_without_prompt_or_retry(tmp_path, monkeypatch):
    resolve = AsyncMock(side_effect=GitIdentityRequired(["user.email"]))
    monkeypatch.setattr("Sprout.execution.git_identity.resolve_commit_contract", resolve)
    result = CliRunner().invoke(app, inputs(tmp_path))
    assert result.exit_code == 2
    assert json.loads(result.output)["missing_fields"] == ["user.email"]
    assert resolve.await_count == 1


def test_cancel_prompt_does_not_retry(tmp_path, monkeypatch):
    resolve = AsyncMock(side_effect=GitIdentityRequired(["user.name"]))
    monkeypatch.setattr("Sprout.execution.git_identity.resolve_commit_contract", resolve)
    result = CliRunner().invoke(app, inputs(tmp_path) + ["--interactive"], input="")
    assert result.exit_code != 0
    assert resolve.await_count == 1
