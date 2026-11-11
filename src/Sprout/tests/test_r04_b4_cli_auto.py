"""Actual REPL automatic analysis; directory/open substitutes are explicitly controlled."""
import pytest

from Sprout.execution import file_access
from Sprout.execution.file_snapshot import DirectoryEntry, ResourceIdentity
from Sprout.security.access import AccessDecision
from Sprout.security.layered_policy import LayeredPolicyEngine, PolicyLayer, PolicyRule
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b4_cli_analysis import cli as cli
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.fixture
def automatic(cli, monkeypatch):
    cli.state.inputs = ["分析当前整个项目"]
    cli.state.answers = [True]
    policy = LayeredPolicyEngine(workspace=PolicyLayer("auto-host", (
        PolicyRule(action="directory.list", decision=AccessDecision.ALLOW, id="host-directory"),
        PolicyRule(action="file.read", path_glob="*private*", decision=AccessDecision.DENY,
                   id="host-private-deny"),)))
    cli.rig.runtime._policy_engine = policy
    cli.rig.runtime._file_broker._policy = policy
    records = {
        ".": [("src", True), ("src2", True)],
        "src": [("src/main.py", False), ("src/worker.py", False)],
        "src2": [("src2/outside.py", False)],
    }
    async def listing(root, target, limit, authorize):
        relative = target.relative_to(root).as_posix()
        result = []
        for label, directory in records.get(relative, []):
            path = root / label
            if await authorize(path):
                if directory:
                    identity = ResourceIdentity.from_stat(path.stat())
                else:
                    import os
                    with path.open("rb") as stream:
                        identity = ResourceIdentity.from_stat(os.fstat(stream.fileno()))
                result.append(DirectoryEntry(label, directory, identity))
        return tuple(result)
    monkeypatch.setattr(file_access, "directory_entries", listing)
    return cli


@pytest.mark.parametrize("request_text", ["分析当前整个项目", "你能分析当前项目吗"])
async def test_whole_project_natural_language_repl_not_manual_manifest(automatic, request_text):
    cli = automatic
    cli.state.inputs = [request_text]
    await cli.run()
    cli.create.assert_awaited_once()
    kwargs = cli.create.call_args.kwargs
    assert kwargs["delegation_scope"].allowed_paths == (str(cli.rig.root),)
    assert "directory.list" in kwargs["delegation_scope"].allowed_actions
    assert not kwargs["metadata"].get("required_paths")
    assert cli.state.classification == 1
    assert any("Indexed counts" in value and "Files: 3" in value
               for value in cli.state.display)
    assert not any("CLI_PROJECT_MAIN_RESULT" in value for value in cli.state.display)
    assert set(cli.captured.opens) == {"src/main.py", "src/worker.py", "src2/outside.py"}
    assert any("覆盖" in value or "coverage" in value.lower() for value in cli.state.display)


async def test_whole_project_can_limit_to_known_subtree(automatic):
    cli = automatic
    cli.state.inputs = ["分析 src 子目录的项目结构"]
    cli.state.answers = [True]
    await cli.run()
    assert cli.create.call_args.kwargs["delegation_scope"].allowed_paths == (
        str(cli.rig.root / "src"),)
    assert set(cli.captured.opens) == {"src/main.py", "src/worker.py"}


async def test_whole_project_query_uses_current_discovery(automatic):
    cli = automatic
    cli.state.query = "symbols"
    await cli.run()
    assert any("cli-query" == m.tool_call_id and "main" in m.content
               for m in cli.state.seen if str(m.role) == "tool")


@pytest.mark.parametrize("answers", [[False], [None], [KeyboardInterrupt()]])
async def test_auto_cancel_or_invalid_does_not_create_task(automatic, answers):
    cli = automatic
    cli.state.answers = answers
    await cli.run()
    cli.create.assert_not_called()
    assert not cli.captured.opens


async def test_auto_requires_real_server_directory_grant(automatic):
    cli = automatic
    from Sprout.security.engine import PolicyEngine
    cli.rig.runtime._policy_engine = PolicyEngine()
    cli.rig.runtime._file_broker._policy = cli.rig.runtime._policy_engine
    await cli.run()
    assert not cli.captured.opens
    assert any("directory" in value.lower() or "目录" in value for value in cli.state.display)


async def test_auto_platform_failure_does_not_publish_success(automatic, monkeypatch):
    cli = automatic
    async def unavailable(*args):
        raise file_access.FileBoundaryError("directory_enumeration_unavailable")
    monkeypatch.setattr(file_access, "directory_entries", unavailable)
    await cli.run()
    assert not cli.captured.opens
    assert not any("Indexed counts" in value for value in cli.state.display)


@pytest.fixture
async def durable_offer_cli(automatic, tmp_path, monkeypatch):
    from Sprout.rootstock.backends.sqlite_store import open_session_store

    cli = automatic
    runtime = cli.rig.runtime
    store = open_session_store(str(tmp_path / "offer.db"))
    await store.save_session(cli.rig.session)
    monkeypatch.setattr(runtime, "_session_store", store)
    monkeypatch.setattr(runtime, "_sessions", type(runtime._sessions)(store))
    cli.state.expected_prompt_classifications = 0
    try:
        yield cli
    finally:
        store.close()


@pytest.mark.parametrize(("answers", "consent", "expected"), [
    (["需要"], True, True),
    (["需要", "需要"], True, True),
    (["需要"], False, False),
    (["不用了", "需要"], True, False),
    (["你好", "需要"], True, False),
])
async def test_capability_offer_requires_followup_and_directory_consent(
    durable_offer_cli, answers, consent, expected,
):
    cli = durable_offer_cli
    cli.state.inputs = ["你是谁你能做什么", *answers]
    cli.state.answers = [consent]
    await cli.run()
    assert any("需要我先看一下当前项目吗" in value for value in cli.state.display)
    assert cli.create.await_count == int(expected)
    assert bool(cli.captured.opens) == expected
    if expected:
        assert any("Indexed counts" in value for value in cli.state.display)


@pytest.mark.parametrize("case", ["other_session", "expired", "no_offer"])
async def test_short_reply_cannot_consume_unavailable_project_offer(durable_offer_cli, case):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from Sprout.message.models import Message

    cli = durable_offer_cli
    runtime = cli.rig.runtime
    session = cli.rig.session
    provider = AsyncMock(return_value=None)
    if case != "no_offer":
        offer = await runtime.handle(
            Message("你是谁你能做什么", session_id=session.id),
            principal=cli.rig.principal, analysis_task_provider=provider,
        )
        assert offer.metadata["clarification_required"]
    if case == "other_session":
        session = replace(session, id="different-session")
        await runtime._session_store.save_session(session)
    elif case == "expired":
        await runtime._session_store._db.execute(
            "UPDATE intent_clarifications_v1 SET expires_at=? WHERE session_id=?",
            ("2000-01-01T00:00:00+00:00", session.id),
        )
    await runtime.handle(
        Message("需要", session_id=session.id),
        principal=cli.rig.principal, analysis_task_provider=provider,
    )
    provider.assert_not_awaited()
    cli.create.assert_not_called()
    assert not cli.captured.opens


@pytest.mark.parametrize(("failure", "expected_reason"), [
    (file_access.FileBoundaryError("secure_open_refused:5"), "directory_access_denied"),
    (file_access.FileBoundaryError("directory_budget_exceeded"), "directory_budget_exceeded"),
    (file_access.FileBoundaryError("directory_identity_changed"), "directory_identity_changed"),
    (file_access.FileBoundaryError("directory_link_or_special_file"),
     "directory_link_or_special_file"),
    (PermissionError("directory_authorization_denied"), "directory_authorization_denied"),
    (OSError("private filename must not escape"), "directory_enumeration_unavailable"),
])
async def test_discovery_failure_reason_is_safe_and_still_denied(
    automatic, monkeypatch, failure, expected_reason,
):
    cli = automatic
    reasons = []
    async def unavailable(*args):
        raise failure
    monkeypatch.setattr(file_access, "directory_entries", unavailable)
    broker = cli.rig.runtime._file_broker
    original = broker.enumerate_readable
    async def observed(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert not result.ok
        assert result.entries == ()
        reasons.append(result.reason)
        return result
    monkeypatch.setattr(broker, "enumerate_readable", observed)
    await cli.run()
    assert reasons == [expected_reason]
    assert not cli.captured.opens
    assert not any("Indexed counts" in value for value in cli.state.display)
    assert not any("private filename" in value for value in cli.state.display)
