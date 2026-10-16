import pytest

from Sprout.gateway.identity import Principal
from Sprout.gateway.local_identity import creator_blockers, creator_record, current_cli_principal


def test_os_identity_does_not_use_user_environment(monkeypatch):
    before = current_cli_principal()
    monkeypatch.setenv("USERNAME", "attacker")
    monkeypatch.setenv("USER", "attacker")
    monkeypatch.setenv("LOGNAME", "attacker")
    assert current_cli_principal() == before
    assert before.authenticated and before.source == "cli"


def test_only_authenticated_cli_creator_matches():
    owner = Principal(user_id="owner", authenticated=True, source="cli")
    record = creator_record(owner)
    assert creator_blockers(record, owner) == []
    assert creator_blockers(record, Principal(user_id="other", authenticated=True, source="cli"))
    assert creator_blockers(record, Principal(user_id="owner", source="cli"))
    assert creator_blockers(record, Principal(user_id="owner", authenticated=True, source="web"))
    assert creator_blockers(None, owner) == ["trusted_creator_missing"]


def test_untrusted_creator_cannot_be_recorded():
    with pytest.raises(ValueError):
        creator_record(Principal(user_id="self-declared"))


@pytest.mark.parametrize("principal", [None, Principal(user_id="self-declared", source="web")])
async def test_replay_library_does_not_promote_remote_caller(tmp_path, monkeypatch, principal):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.evolution.replay_runner import ReplayRunner

    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    runtime = SimpleNamespace(get_task=AsyncMock(return_value=SimpleNamespace(id="source")),
                              get_workspace=AsyncMock())
    result = await ReplayRunner(runtime).run(
        "source", revision="a" * 40, verification_commands=("pytest",),
        regression_commands=("pytest",), settings=object(), output_dir=tmp_path,
        cli_principal=principal,
    )
    assert result.status == "waiting_resource" and not result.task_id
    runtime.get_workspace.assert_not_awaited()
