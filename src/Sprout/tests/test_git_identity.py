from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from Sprout.evolution.isolated_replay import git
from Sprout.execution.git_identity import GitIdentityRequired, resolve_commit_contract
from Sprout.gateway.identity import Principal
from Sprout.tests.test_coding_assembly import fixture


async def configured(tmp_path, monkeypatch):
    store, _, task, _, _ = await fixture(tmp_path, monkeypatch)
    payload = task.metadata["replay_plan"]["payload"]
    root = Path(payload["original_root"])
    git(root, "init", "-q")
    monkeypatch.setattr("Sprout.execution.git_identity.current_cli_principal", lambda: task.actor)
    plan = {"operation": "commit", "write_authorized": False,
            "target": payload["repository_root"]}
    return store, task.metadata["replay_plan"], plan, root


async def test_reads_existing_project_identity_without_config_changes(tmp_path, monkeypatch):
    store, replay, plan, root = await configured(tmp_path, monkeypatch)
    try:
        git(root, "config", "--local", "user.name", "Fixture Operator")
        git(root, "config", "--local", "user.email", "fixture@invalid.example")
        before = (root / ".git/config").read_bytes()
        contract = await resolve_commit_contract(replay, plan, message="Explicit fixture")
        assert contract["author_name"] == "Fixture Operator"
        assert contract["author_email"] == "fixture@invalid.example"
        assert (root / ".git/config").read_bytes() == before
    finally:
        store.close()


async def test_missing_identity_returns_guidance_without_inventing_values(tmp_path, monkeypatch):
    store, replay, plan, root = await configured(tmp_path, monkeypatch)
    try:
        read = AsyncMock(side_effect=[None, None])
        monkeypatch.setattr("Sprout.execution.git_identity._read_config", read)
        with pytest.raises(GitIdentityRequired) as exc:
            await resolve_commit_contract(replay, plan, message="Explicit fixture")
        guidance = exc.value.guidance(root)
        assert guidance["missing_fields"] == ["user.name", "user.email"]
        assert guidance["automatic_retry"] is False and guidance["cancel_available"] is True
        assert all(argv[4] == "--local" for argv in guidance["configuration_commands"])
        assert guidance["read_only"] is True
    finally:
        store.close()


async def test_foreign_operator_cannot_read_identity_for_experiment(tmp_path, monkeypatch):
    store, replay, plan, _ = await configured(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr("Sprout.execution.git_identity.current_cli_principal",
            lambda: Principal(user_id="foreign", source="cli", authenticated=True))
        read = AsyncMock()
        monkeypatch.setattr("Sprout.execution.git_identity._read_config", read)
        with pytest.raises(ValueError, match="creator"):
            await resolve_commit_contract(replay, plan, message="Explicit fixture")
        read.assert_not_called()
    finally:
        store.close()


async def test_explicit_identity_does_not_read_or_change_config(tmp_path, monkeypatch):
    store, replay, plan, root = await configured(tmp_path, monkeypatch)
    try:
        before = (root / ".git/config").read_bytes()
        read = AsyncMock()
        monkeypatch.setattr("Sprout.execution.git_identity._read_config", read)
        contract = await resolve_commit_contract(replay, plan, message="Operator choice",
            author_name="Explicit Operator", author_email="explicit@invalid.example")
        assert contract["author_name"] == "Explicit Operator"
        read.assert_not_called()
        assert (root / ".git/config").read_bytes() == before
    finally:
        store.close()
