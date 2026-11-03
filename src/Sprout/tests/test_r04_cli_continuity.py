"""Conversation history is data; project continuation requires settled source."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from Sprout.message.models import Message
from Sprout.session.models import Session, Turn
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b4_cli_analysis import cli as cli
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


def test_followup_distinguishes_scan_negation_from_new_scan():
    from Sprout.runtime.dialogue import analysis_followup

    assert analysis_followup("解释核心模块之间的依赖关系，不重新扫描，不修改代码")
    assert not analysis_followup("重新扫描项目，解释核心模块之间的依赖关系")


async def test_project_answer_is_available_without_second_analysis(cli, monkeypatch):
    cli.state.inputs = ["分析 src/main.py 的依赖", "进一步分析核心模块", "解释模块之间的依赖"]
    cli.state.intents = ["workspace", "conversation", "workspace"]
    cli.state.answers = [True]
    calls = []
    original = cli.model.chat

    async def observed(messages, **kwargs):
        if "Classify the user's intent" not in str(messages[0].content):
            calls.append((messages, kwargs))
        return await original(messages, **kwargs)

    monkeypatch.setattr(cli.model, "chat", observed)
    await cli.run()
    await asyncio.gather(*tuple(cli.rig.runtime._background_tasks))
    cli.create.assert_awaited_once()
    assert cli.captured.opens == ["src/main.py"]
    assert len(calls) == 3
    for messages, kwargs in calls[1:]:
        assert any("CLI_PROJECT_MAIN_RESULT" in m.content for m in messages)
        assert not kwargs.get("tools")


async def test_owned_persisted_dialogue_restores_after_runtime_recreation(tmp_path):
    from Sprout.gateway.identity import Principal
    from Sprout.rootstock.backends.sqlite_store import open_session_store
    from Sprout.storage.bundle import StorageBundle
    from Sprout.tests.conftest import build_runtime

    principal = Principal("owner", source="cli", authenticated=True)

    def runtime():
        storage = StorageBundle.in_memory()
        storage.sessions = open_session_store(str(tmp_path / "session.db"))
        return build_runtime(storage=storage)[0]

    first = runtime()
    session = await first.create_session("owner", principal=principal)
    await first.handle(Message("Remember my preferred name: Ada", session_id=session.id),
                       principal=principal)
    await first.storage.close()
    second = runtime()
    try:
        stored = await second.get_session(session.id)
        context = await second._ordinary_conversation_context(
            Message("what did I say?", user_id="owner", session_id=session.id),
            stored, {}, {})
        assert [turn.role for turn in context.memory.working] == ["user", "assistant"]
        assert "Ada" in context.memory.working[0].content
        assert not context.memory.working[0].metadata
        assert not second._original_user_turns
        assert not second._ordinary_assistant_turns
    finally:
        await second.storage.close()


async def test_history_is_bounded_ordered_session_local_and_not_tool_authority(cli):
    runtime = cli.rig.runtime
    session = cli.rig.session
    for seq in range(40):
        await runtime._session_store.append_turn(Turn(
            session.id, "user" if seq % 2 == 0 else "assistant", f"turn-{seq}"))
    await runtime._session_store.save_session(Session("other", "owner"))
    await runtime._session_store.append_turn(Turn("other", "user", "OTHER_SECRET"))
    await runtime._session_store.append_turn(Turn(session.id, "tool", "RAW_TOOL_RESULT"))
    message = Message("hello", user_id="owner", session_id=session.id)
    context = await runtime._ordinary_conversation_context(message, session, {}, {})
    contents = [turn.content for turn in context.memory.working]
    assert contents and len(contents) <= runtime._contexts._settings.working_window
    assert "OTHER_SECRET" not in contents and "RAW_TOOL_RESULT" not in contents
    assert [turn.seq for turn in context.memory.working] == sorted(
        turn.seq for turn in context.memory.working)
    assert await runtime._ordinary_conversation_context(
        replace(message, user_id="other"), session, {}, {}) is None


async def test_forged_project_reference_is_not_admitted(cli):
    runtime = cli.rig.runtime
    session = cli.rig.session
    session.metadata["project_analysis_ref_v1"] = {"task_id": "missing", "node_id": "fake"}
    await runtime._session_store.save_session(session)
    context = await runtime._ordinary_conversation_context(
        Message("进一步分析核心模块", user_id="owner", session_id=session.id), session, {}, {})
    assert not context.metadata.get("project_analysis_followup")


async def test_read_clarification_keeps_completed_project_source_without_tools(cli, tmp_path):
    from Sprout.rootstock.backends.sqlite_store import open_session_store
    from Sprout.runtime.clarification import _ask
    from Sprout.session.manager import SessionManager

    runtime = cli.rig.runtime
    sessions = open_session_store(str(tmp_path / "clarification.db"))
    await sessions.save_session(cli.rig.session)
    runtime.storage.sessions = runtime._session_store = sessions
    runtime._sessions = SessionManager(sessions)
    runtime._contexts._session_store = sessions
    cli.state.inputs = ["分析 src/main.py 的依赖"]
    cli.state.answers = [True]
    await cli.run()
    session = await runtime.get_session(cli.rig.session.id)
    await _ask(runtime, Message("进一步分析核心模块，找出值得修复的问题", session_id=session.id,
                                user_id=cli.rig.principal.user_id, channel="cli"),
               session, cli.rig.principal)
    cli.state.seen.clear()
    result = await runtime.handle(Message("只分析", session_id=session.id),
                                  principal=cli.rig.principal)
    assert result.metadata["analysis_scope"] == "completed_project_analysis"
    assert any("CLI_PROJECT_MAIN_RESULT" in m.content for m in cli.state.seen)
    assert cli.captured.opens == ["src/main.py"]
    cli.create.assert_awaited_once()


async def test_cancellation_keeps_context_and_does_not_grant_code_scope(cli):
    cli.state.inputs = ["分析 src/main.py 的依赖", "你好"]
    cli.state.intents = ["workspace", "conversation"]
    cli.state.answers = [False]
    await cli.run()
    cli.create.assert_not_called()
    assert not cli.captured.opens
    assert "CLI_PROJECT_MAIN_RESULT" in cli.state.display


@pytest.mark.parametrize("tamper", ["owner", "session", "scope", "output", "policy"])
async def test_project_source_rechecked_not_rebuilt_from_metadata(cli, tamper):
    from Sprout.runtime.dialogue import project_answer
    from Sprout.security.access import AccessDecision
    from Sprout.security.layered_policy import LayeredPolicyEngine, PolicyLayer, PolicyRule

    cli.state.inputs = ["分析 src/main.py 的依赖"]
    cli.state.answers = [True]
    await cli.run()
    runtime, principal = cli.rig.runtime, cli.rig.principal
    session = await runtime.get_session(cli.rig.session.id)
    assert await project_answer(runtime, session, principal) == "CLI_PROJECT_MAIN_RESULT"
    task = await runtime.get_task(session.metadata["project_analysis_ref_v1"]["task_id"])
    if tamper == "owner":
        principal = replace(principal, user_id="other")
    elif tamper == "session":
        session = replace(session, id="other-session")
    elif tamper == "scope":
        await runtime.storage.metadata.save_task(replace(task, delegation_scope=replace(
            task.delegation_scope, expires_at=datetime.now(UTC) - timedelta(seconds=1))))
    elif tamper == "output":
        node_id = session.metadata["project_analysis_ref_v1"]["node_id"]
        # Negative corruption in this isolated SQLite only; never issue a source.
        await runtime.storage.metadata._db.execute(
            "UPDATE execution_nodes SET "
            "metadata_json=json_set(metadata_json, '$.output.content', ?) "
            "WHERE id=?", ("forged output", node_id))
    else:
        runtime._policy_engine = LayeredPolicyEngine(workspace=PolicyLayer("revoke", (
            PolicyRule(action="file.read", decision=AccessDecision.DENY),)))
    assert await project_answer(runtime, session, principal) is None
    assert cli.captured.opens == ["src/main.py"]
