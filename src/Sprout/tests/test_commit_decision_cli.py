import asyncio
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from Sprout.cli.commands import evolution
from Sprout.tests.test_commit_status import arranged


def command(name, path, request):
    return [name, request["task_id"], request["id"], "--database", str(path)]


async def test_review_redacts_display_without_changing_frozen_fingerprint(tmp_path, monkeypatch):
    secret = "sk-abcdefghijklmnopqrstuvwx"
    path, metadata, operational, request, principal = await arranged(tmp_path, review_secret=secret)
    try:
        monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal",
                            lambda: principal)
        result = CliRunner().invoke(evolution.app, command("commit-review", path, request))
        assert result.exit_code == 0, result.output
        assert secret not in result.output and "[REDACTED]" in result.output
        displayed = json.loads(result.output)["requests"][0]
        assert displayed["decision_fingerprint"] == request["fingerprint"]
        assert displayed["display_redacted"] is True
        assert (await operational.get_commit_request(request["id"]))["review"] == request["review"]
    finally:
        metadata.close()
        operational.close()


async def test_noninteractive_requires_explicit_decision_and_fingerprint(tmp_path, monkeypatch):
    path, metadata, operational, request, principal = await arranged(tmp_path)
    try:
        monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal",
                            lambda: principal)
        args = command("commit-decide", path, request)
        missing = await asyncio.to_thread(CliRunner().invoke, evolution.app, args)
        assert missing.exit_code == 1
        stale = await asyncio.to_thread(CliRunner().invoke, evolution.app,
            args + ["--decision", "approve", "--expected-fingerprint", "stale"])
        assert stale.exit_code == 1
        assert (await operational.get_commit_request(request["id"]))["state"] == "pending"
        confirmed = await asyncio.to_thread(CliRunner().invoke, evolution.app,
            args + ["--decision", "approve", "--expected-fingerprint", request["fingerprint"]])
        assert confirmed.exit_code == 0, confirmed.output
        assert json.loads(confirmed.output)["local_execution"] is False
        persisted = await operational.get_commit_request(request["id"])
        assert persisted["dispatch_state"] == "waiting_schedule"
        assert await operational.get_commit_dispatch(request["id"]) is None
        assert await operational.list_approvals() == []
    finally:
        metadata.close()
        operational.close()


async def test_interactive_default_cancel_changes_nothing(tmp_path, monkeypatch):
    path, metadata, operational, request, principal = await arranged(tmp_path)
    try:
        monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal",
                            lambda: principal)
        monkeypatch.setattr(evolution, "sys", SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: True),
            stdout=SimpleNamespace(isatty=lambda: True)))
        result = await asyncio.to_thread(CliRunner().invoke, evolution.app,
            command("commit-decide", path, request), input="\n")
        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output
        assert (await operational.get_commit_request(request["id"]))["state"] == "pending"
    finally:
        metadata.close()
        operational.close()


@pytest.mark.parametrize("choice,state", [("approve", "approved"), ("reject", "rejected")])
async def test_interactive_decision_is_persisted_once(tmp_path, monkeypatch, choice, state):
    path, metadata, operational, request, principal = await arranged(tmp_path)
    try:
        monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal",
                            lambda: principal)
        monkeypatch.setattr(evolution, "sys", SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: True),
            stdout=SimpleNamespace(isatty=lambda: True)))
        args = command("commit-decide", path, request)
        result = await asyncio.to_thread(CliRunner().invoke, evolution.app, args,
                                         input=choice + "\n")
        assert result.exit_code == 0, result.output
        assert (await operational.get_commit_request(request["id"]))["state"] == state
        repeated = await asyncio.to_thread(CliRunner().invoke, evolution.app,
            args + ["--decision", choice, "--expected-fingerprint", request["fingerprint"]])
        assert repeated.exit_code == 1
        assert await operational.get_commit_dispatch(request["id"]) is None
    finally:
        metadata.close()
        operational.close()
