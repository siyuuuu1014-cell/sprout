"""Clarification is durable intent selection, never workspace authorization."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.agent.base import AgentResult
from Sprout.gateway.identity import Principal
from Sprout.llm.echo import EchoModel
from Sprout.message.models import Message
from Sprout.rootstock.backends.sqlite_store import SqliteSessionStore
from Sprout.session.manager import SessionManager
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.tests.test_r02_trusted_intent_audit import audit_runtime as audit_runtime

OPERATOR = Principal("operator", source="cli")
AMBIGUOUS = "帮我看看这个项目的登录功能。"


@pytest.fixture
def clarification_runtime(audit_runtime, tmp_path):
    runtime, tasks, events = audit_runtime
    store = SqliteSessionStore(SqliteDatabase(tmp_path / "conversation.db"))
    runtime._session_store = store
    runtime.storage.sessions = store
    runtime._sessions = SessionManager(store)
    runtime.models.register(EchoModel(), default=True)
    agent = AsyncMock()
    agent.run.return_value = AgentResult("ordinary response")
    # A stream-less test agent: handle_stream must fall back to run.
    del agent.stream
    runtime.register_agent("clarification-test", agent, default=True)
    runtime.open_workspace = AsyncMock()
    yield runtime, tasks, events, agent
    store.close()


async def deliver(runtime, text, session=None, *, stream=False, principal=OPERATOR, **kwargs):
    message = Message(text, session_id=session, **kwargs)
    if not stream:
        return await runtime.handle(message, principal=principal)
    chunks = [chunk async for chunk in runtime.handle_stream(message, principal=principal)]
    if runtime._background_tasks:
        import asyncio

        await asyncio.gather(*list(runtime._background_tasks))
    return next(chunk.outbound for chunk in chunks if chunk.outbound)


@pytest.mark.parametrize("stream", [False, True])
async def test_ambiguity_asks_without_agent_task_workspace_or_write_event(
    clarification_runtime, stream,
):
    runtime, tasks, events, agent = clarification_runtime
    response = await deliver(runtime, AMBIGUOUS, stream=stream)
    assert response.metadata.get("clarification_required") is True
    assert "登录" in response.content and "修改" in response.content
    assert not response.metadata.get("workspace_consent_required")
    assert not any(event.name == "intent.task.requested" for event in events)
    agent.run.assert_not_awaited()
    runtime.open_workspace.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    assert tasks == []
    assert await runtime._session_store.database.scalar(
        "SELECT COUNT(*) FROM workspace_consents_v1"
    ) == 0
    history = await runtime.history(response.session_id)
    assert [turn.content for turn in history] == [AMBIGUOUS, response.content]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("answer", ["只分析", "直接修改", "先别做了", "我只是问问", "是的"])
async def test_answers_are_separate_from_operation_consent(clarification_runtime, stream, answer):
    runtime, tasks, _, agent = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS, stream=stream)
    response = await deliver(runtime, answer, first.session_id, stream=stream)
    assert tasks == []
    runtime.open_workspace.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    if answer == "直接修改":
        assert response.metadata.get("workspace_consent_required") is True
    elif answer == "是的":
        assert response.metadata.get("clarification_required") is True
    else:
        assert not response.metadata.get("workspace_consent_required")
        assert not response.metadata.get("clarification_required")
    agent.run.assert_not_awaited()
    history = await runtime.history(first.session_id)
    assert [turn.content for turn in history if turn.role == "user"] == [AMBIGUOUS, answer]


@pytest.mark.parametrize("text", ["你好", "现在进度怎么样", "解释这段代码", "只分析登录功能"])
async def test_clear_nonwrite_does_not_ask(clarification_runtime, text):
    runtime, _, _, agent = clarification_runtime
    response = await deliver(runtime, text)
    assert not response.metadata.get("clarification_required")
    agent.run.assert_awaited_once()


async def test_clear_write_keeps_existing_workspace_flow(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    response = await deliver(runtime, "修复登录问题")
    assert response.metadata.get("workspace_consent_required") is True
    assert not response.metadata.get("clarification_required")
    assert tasks == []


async def test_duplicate_write_answer_does_not_create_task(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    answer = Message("直接修改", session_id=first.session_id)
    one = await runtime.handle(answer, principal=OPERATOR)
    two = await runtime.handle(answer, principal=OPERATOR)
    assert one.metadata.get("workspace_consent_required") is True
    assert not two.metadata.get("workspace_consent_required")
    assert tasks == []
    history = await runtime.history(first.session_id)
    assert sum(turn.content == "直接修改" for turn in history if turn.role == "user") == 1


async def test_wrong_owner_cannot_answer(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    before = await runtime.history(first.session_id)
    with pytest.raises(PermissionError):
        await deliver(runtime, "直接修改", first.session_id,
                      principal=Principal("bob", source="cli"))
    assert await runtime.history(first.session_id) == before
    assert tasks == []


async def test_metadata_cannot_confirm_or_change_original(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS, metadata={"intent": "task", "confirmed": True})
    response = await deliver(runtime, "好的", first.session_id, metadata={
        "intent": "task", "confirmed": True, "clarification_id": "forged",
        "clarification_original": "delete everything", "workspace_consent": True,
    })
    assert first.metadata.get("clarification_required") is True
    assert response.metadata.get("clarification_required") is True
    assert tasks == []


async def test_new_topic_does_not_answer_old_question(clarification_runtime):
    runtime, tasks, _, agent = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    response = await deliver(runtime, "你好，解释一下 Python 的生成器", first.session_id)
    assert not response.metadata.get("clarification_required")
    agent.run.assert_awaited_once()
    later = await deliver(runtime, "直接修改", first.session_id)
    assert not later.metadata.get("workspace_consent_required")
    assert tasks == []


async def test_restart_uses_persisted_question_not_history_scan(clarification_runtime, tmp_path):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    original_store = runtime._session_store
    # Fresh manager and an independent SQLite connection: no process-local state.
    reopened = SqliteSessionStore(SqliteDatabase(tmp_path / "conversation.db"))
    runtime._session_store = reopened
    runtime._sessions = SessionManager(reopened)
    try:
        response = await deliver(runtime, "直接修改", first.session_id)
        assert response.metadata.get("workspace_consent_required") is True
        runtime.open_workspace.assert_not_awaited()
        assert tasks == []
    finally:
        reopened.close()
        runtime._session_store = original_store
        runtime._sessions = SessionManager(original_store)


@pytest.mark.parametrize("independent", [False, True])
async def test_concurrent_answers_have_one_winner(clarification_runtime, tmp_path, independent):
    from copy import copy

    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    other = copy(runtime) if independent else runtime
    original_store = runtime._session_store
    if independent:
        other._session_store = SqliteSessionStore(SqliteDatabase(tmp_path / "conversation.db"))
        other._sessions = SessionManager(other._session_store)
    try:
        responses = await asyncio.gather(
            deliver(runtime, "直接修改", first.session_id),
            deliver(other, "直接修改", first.session_id),
        )
        assert sum(bool(r.metadata.get("workspace_consent_required")) for r in responses) == 1
        assert tasks == []
        runtime.open_workspace.assert_not_awaited()
        history = await runtime.history(first.session_id)
        assert sum(t.role == "user" and t.content == "直接修改" for t in history) == 1
        assert await original_store.database.scalar(
            "SELECT COUNT(*) FROM workspace_consents_v1"
        ) == 1
    finally:
        if independent:
            other._session_store.close()


async def test_selected_write_requires_separate_durable_consent(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    await deliver(runtime, "直接修改", first.session_id)
    assert tasks == []
    runtime.open_workspace.return_value = SimpleNamespace(id="audit-workspace")
    response = await runtime.grant_workspace_consent(first.session_id, principal=OPERATOR)
    assert response.metadata.get("task_id")
    assert len(tasks) == 1 and AMBIGUOUS in tasks[0].instruction
    assert tasks[0].actor.user_id == OPERATOR.user_id
    assert tasks[0].actor.source == OPERATOR.source
    runtime.execute.assert_not_awaited()
    runtime.submit_task.assert_awaited_once()
    with pytest.raises(LookupError):
        await runtime.grant_workspace_consent(first.session_id, principal=OPERATOR)
    assert len(tasks) == 1


@pytest.mark.parametrize("choice", ["只分析", "先别做了", "我只是问问"])
async def test_nonwrite_choice_cannot_later_be_granted_workspace(clarification_runtime, choice):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    await deliver(runtime, choice, first.session_id)
    with pytest.raises(LookupError):
        await runtime.grant_workspace_consent(first.session_id, principal=OPERATOR)
    assert tasks == []
    runtime.open_workspace.assert_not_awaited()


@pytest.mark.parametrize("mutation", ["owner", "source", "authenticated", "content"])
async def test_changed_authority_does_not_resolve(clarification_runtime, mutation):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    store = runtime._session_store
    session = await runtime.get_session(first.session_id)
    if mutation == "content":
        await store.database.execute(
            "UPDATE turns SET content='tampered' WHERE session_id=? AND role='user'",
            (session.id,),
        )
    else:
        if mutation == "owner":
            session.user_id = "other"
        else:
            session.metadata["entry_principal_v1"][mutation] = (
                "cron" if mutation == "source" else True
            )
        await store.save_session(session)
    try:
        response = await deliver(runtime, "直接修改", first.session_id)
    except PermissionError:
        pass
    else:
        assert not response.metadata.get("workspace_consent_required")
    runtime.open_workspace.assert_not_awaited()
    assert tasks == []


async def test_same_owner_different_principal_cannot_resolve(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    with pytest.raises(PermissionError):
        await deliver(runtime, "直接修改", first.session_id,
                      principal=Principal("operator", source="web", authenticated=True))
    assert tasks == []


async def test_answer_in_other_session_cannot_claim_original(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    second = await deliver(runtime, "你好")
    response = await deliver(runtime, "直接修改", second.session_id,
                             metadata={"clarification_id": first.metadata["clarification_id"]})
    assert not response.metadata.get("workspace_consent_required")
    record = await runtime._session_store.get_intent_clarification(first.session_id)
    assert record["state"] == "pending"
    assert tasks == []


async def test_expired_question_never_authorizes_write(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    await runtime._session_store.database.execute(
        "UPDATE intent_clarifications_v1 SET expires_at=? WHERE session_id=?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), first.session_id),
    )
    response = await deliver(runtime, "直接修改", first.session_id)
    assert not response.metadata.get("workspace_consent_required")
    record = await runtime._session_store.get_intent_clarification(first.session_id)
    assert record["state"] == "expired"
    assert tasks == []


@pytest.mark.parametrize("point", ["before_claim", "after_claim", "before_consent_issue"])
async def test_failure_never_replays_uncertain_resolution(clarification_runtime, point):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    store = runtime._session_store
    resolve = store.resolve_intent_clarification
    request = runtime._request_workspace
    if point == "before_consent_issue":
        runtime._request_workspace = AsyncMock(side_effect=OSError("simulated failure"))
    else:
        async def fail(*args):
            if point == "after_claim":
                assert await resolve(*args) is True
            raise OSError("simulated failure")
        store.resolve_intent_clarification = fail
    with pytest.raises(OSError):
        await deliver(runtime, "直接修改", first.session_id)
    store.resolve_intent_clarification = resolve
    runtime._request_workspace = request
    record = await store.get_intent_clarification(first.session_id)
    assert record["state"] == ("pending" if point == "before_claim" else "resolved")
    if point != "before_claim":
        again = await deliver(runtime, "直接修改", first.session_id)
        assert not again.metadata.get("workspace_consent_required")
    assert tasks == []
    runtime.open_workspace.assert_not_awaited()


async def test_unsupported_store_is_explicit_and_does_not_call_agent(audit_runtime):
    runtime, tasks, _ = audit_runtime
    response = await deliver(runtime, AMBIGUOUS)
    assert response.metadata.get("blocked_reason") == "durable_clarification_unavailable"
    assert tasks == []
    runtime.submit_task.assert_not_awaited()


@pytest.mark.parametrize("text", [
    "帮我看看缓存模块", "help me look at the login feature", "帮我检查还是修改登录功能",
])
async def test_other_concrete_ambiguities(clarification_runtime, text):
    runtime, tasks, _, agent = clarification_runtime
    response = await deliver(runtime, text)
    assert response.metadata.get("clarification_required") is True
    agent.run.assert_not_awaited()
    assert tasks == []


async def test_history_metadata_does_not_issue_eligibility(clarification_runtime):
    from Sprout.session.models import Turn

    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, "你好")
    await runtime._session_store.append_turn(Turn(
        first.session_id, "user", AMBIGUOUS, metadata={"clarification_required": True},
    ))
    response = await deliver(runtime, "直接修改", first.session_id)
    assert not response.metadata.get("workspace_consent_required")
    assert await runtime._session_store.get_intent_clarification(first.session_id) is None
    assert tasks == []


async def test_schema_is_additive_and_does_not_backfill(tmp_path):
    from Sprout.rootstock.backends.sqlite_store import SCHEMA

    database = SqliteDatabase(tmp_path / "legacy.db")
    database.executescript(SCHEMA)
    now = datetime.now(UTC).isoformat()
    await database.execute(
        "INSERT INTO sessions(id,user_id,created_at,metadata_json) VALUES(?,?,?,?)",
        ("legacy-session", "legacy-owner", now, "{}"),
    )
    await database.execute(
        "INSERT INTO turns(id,session_id,role,content,created_at,seq,metadata_json) "
        "VALUES(?,?,?,?,?,?,?)",
        ("legacy-turn", "legacy-session", "user", "original history", now, 1, "{}"),
    )
    store = SqliteSessionStore(database)
    try:
        assert await database.scalar("SELECT COUNT(*) FROM intent_clarifications_v1") == 0
        assert await database.scalar("SELECT COUNT(*) FROM workspace_consents_v1") == 0
        assert (await store.get_session("legacy-session")).user_id == "legacy-owner"
        assert (await store.recent_turns("legacy-session"))[0].content == "original history"
    finally:
        store.close()


async def test_record_contains_authority_and_content_digest(clarification_runtime):
    runtime, _, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    record = await runtime._session_store.get_intent_clarification(first.session_id)
    snapshot = json.loads(record["snapshot_json"])
    assert snapshot["request"] == AMBIGUOUS and snapshot["owner"] == OPERATOR.user_id
    assert snapshot["principal"]["source"] == OPERATOR.source
    assert len(snapshot["content_sha256"]) == 64
    assert record["version"] == 1 and record["question"] == first.content


@pytest.mark.parametrize("transport", ["cli", "web", "mcp", "embedded"])
async def test_gateway_conversations_use_same_clarification_gate(
    clarification_runtime, monkeypatch, transport,
):
    from Sprout.gateway.runtime_gateway import RuntimeGateway
    from Sprout.gateway.transport_gateways import CLIGateway, MCPGateway, WebGateway

    runtime, tasks, _, agent = clarification_runtime
    if transport == "cli":
        monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal", lambda: OPERATOR)
        gateway = CLIGateway(runtime)
        first = await gateway.handle_message(Message(AMBIGUOUS))
        session_id = first.metadata["session_id"]
        response = await gateway.handle_message(Message("是的", session_id=session_id))
    elif transport == "mcp":
        # Legacy MCPGateway has no trusted-principal argument. The actual
        # MCP server's sprout_message adapter is covered separately below.
        gateway = MCPGateway(runtime)
        first = await gateway.handle_message(Message(AMBIGUOUS))
        assert first.metadata["blocked_reason"] == "durable_clarification_unavailable"
        agent.run.assert_not_awaited()
        assert tasks == []
        return
    else:
        gateway = {"web": WebGateway, "mcp": MCPGateway}.get(transport)
        gateway = gateway(runtime) if gateway else RuntimeGateway(runtime)
        first = await gateway.handle_message(Message(AMBIGUOUS), principal=OPERATOR)
        session_id = first.metadata["session_id"]
        response = await gateway.handle_message(Message("是的", session_id=session_id),
                                                principal=OPERATOR)
    assert first.metadata["clarification_required"] is True
    assert response.metadata["clarification_required"] is True
    agent.run.assert_not_awaited()
    assert tasks == []


async def test_mcp_session_create_preserves_trusted_source_for_clarification(clarification_runtime):
    from Sprout.mcp.server.tools import register_tools
    from Sprout.tests.test_r02_trusted_intent_audit import ToolCapture

    runtime, tasks, _, _ = clarification_runtime
    server = ToolCapture()
    register_tools(server, runtime)
    session_id = await server.functions["sprout_session_create"]()
    question = await server.functions["sprout_message"](AMBIGUOUS, session_id)
    assert "修改" in question
    response = await server.functions["sprout_message"]("直接修改", session_id)
    assert "工作区" in response and "尚未授予" in response
    session = await runtime.get_session(session_id)
    assert session.metadata["entry_principal_v1"]["source"] == "mcp_client"
    assert tasks == []


async def test_legacy_session_does_not_get_trusted_source_backfilled(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    session = await runtime.create_session(OPERATOR.user_id)
    response = await deliver(runtime, AMBIGUOUS, session.id)
    assert response.metadata["blocked_reason"] == "clarification_identity_unavailable"
    assert "entry_principal_v1" not in (await runtime.get_session(session.id)).metadata
    assert tasks == []


@pytest.mark.parametrize("stream", [False, True])
async def test_web_http_and_websocket_clarification(clarification_runtime, stream):
    from web.webapi.routes.chat import create_chat_routes
    from web.webapi.websocket.chat import create_ws_chat_endpoint

    runtime, tasks, _, agent = clarification_runtime
    principal = Principal("operator", source="web", authenticated=True)
    endpoint = next(route.endpoint for route in create_chat_routes(runtime)
                    if route.path == "/api/chat")
    response = await endpoint(SimpleNamespace(
        json=AsyncMock(return_value={"message": AMBIGUOUS, "user_id": "forged"}),
        state=SimpleNamespace(principal=principal),
    ))
    data = json.loads(response.body)
    assert response.status_code == 200 and "修改" in data["content"]
    websocket = SimpleNamespace(
        accept=AsyncMock(),
        receive_json=AsyncMock(side_effect=[
            {"message": "是的", "session_id": data["session_id"], "stream": stream}, EOFError(),
        ]),
        send_json=AsyncMock(), state=SimpleNamespace(principal=principal),
    )
    await create_ws_chat_endpoint(runtime)(websocket)
    sent = [call.args[0] for call in websocket.send_json.call_args_list]
    assert any("只分析" in str(item.get("content", "")) for item in sent)
    agent.run.assert_not_awaited()
    assert tasks == []


async def test_read_choice_ignores_unsolicited_model_tool_calls(clarification_runtime):
    from Sprout.llm.messages import LLMResponse, ToolCall

    runtime, tasks, _, agent = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    model = SimpleNamespace(name="malicious-model", chat=AsyncMock(return_value=LLMResponse(
        content="analysis only", tool_calls=(ToolCall("one", "file.write", {"path": "x"}),),
    )))
    runtime.models.register(model, default=True)
    response = await deliver(runtime, "只分析", first.session_id)
    assert response.content == (
        "说明：以下仅基于你提供的请求文本，未读取或检查真实仓库文件。\n\nanalysis only"
    )
    assert response.metadata["repository_inspected"] is False
    assert model.chat.call_args.kwargs["tools"] == ()
    agent.run.assert_not_awaited()
    assert tasks == []


async def test_classifier_failure_cannot_escalate_ambiguity(clarification_runtime):
    runtime, tasks, _, agent = clarification_runtime
    runtime._classify_intent_with_llm = AsyncMock(side_effect=RuntimeError("unavailable"))
    response = await deliver(runtime, AMBIGUOUS)
    assert response.metadata["clarification_required"] is True
    runtime._classify_intent_with_llm.assert_not_awaited()
    agent.run.assert_not_awaited()
    assert tasks == []


async def test_delete_session_removes_private_clarification_body(clarification_runtime):
    runtime, _, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    store = runtime._session_store
    await store.delete_session(first.session_id)
    assert await store.get_intent_clarification(first.session_id) is None
    assert await store.recent_turns(first.session_id) == []


@pytest.mark.parametrize("reply", ["只分析", "直接修改", "先别做了"])
async def test_terminal_resolution_never_reopens_on_repeated_answer(clarification_runtime, reply):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    await deliver(runtime, reply, first.session_id)
    record_before = await runtime._session_store.get_intent_clarification(first.session_id)
    second = await deliver(runtime, reply, first.session_id)
    record_after = await runtime._session_store.get_intent_clarification(first.session_id)
    assert record_before == record_after
    assert not second.metadata.get("workspace_consent_required")
    assert tasks == []


async def test_model_unavailable_read_choice_stays_nonwrite(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    model = SimpleNamespace(name="unavailable", chat=AsyncMock(side_effect=OSError("offline")))
    runtime.models.register(model, default=True)
    response = await deliver(runtime, "只分析", first.session_id)
    assert response.metadata.get("clarification_resolution") == "read"
    assert "不可用" in response.content
    assert tasks == []
    runtime.open_workspace.assert_not_awaited()


async def test_sse_question_and_unclear_reply_keep_same_state(clarification_runtime):
    from web.webapi.routes.chat import create_chat_routes

    runtime, tasks, _, agent = clarification_runtime
    principal = Principal("operator", source="web", authenticated=True)
    endpoint = next(route.endpoint for route in create_chat_routes(runtime)
                    if route.path == "/api/chat/stream")

    async def request(text, session_id=None):
        response = await endpoint(SimpleNamespace(
            json=AsyncMock(return_value={"message": text, "session_id": session_id}),
            state=SimpleNamespace(principal=principal),
        ))
        chunks = [chunk async for chunk in response.body_iterator]
        payloads = [json.loads(chunk.removeprefix("data: ").strip()) for chunk in chunks]
        return next(item for item in payloads if item["type"] == "done")

    first = await request(AMBIGUOUS)
    second = await request("是的", first["session_id"])
    assert first["metadata"]["clarification_required"] is True
    assert second["metadata"]["clarification_id"] == first["metadata"]["clarification_id"]
    agent.run.assert_not_awaited()
    assert tasks == []


async def test_new_ambiguous_topic_replaces_scope_not_original_authority(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    second = await deliver(runtime, "帮我看看缓存模块", first.session_id)
    assert first.metadata["clarification_id"] != second.metadata["clarification_id"]
    await deliver(runtime, "直接修改", first.session_id)
    runtime.open_workspace.return_value = SimpleNamespace(id="audit-workspace")
    await runtime.grant_workspace_consent(first.session_id, principal=OPERATOR)
    assert len(tasks) == 1
    assert "缓存" in tasks[0].instruction and "登录" not in tasks[0].instruction
    rows = await runtime._session_store.database.fetchall(
        "SELECT state FROM intent_clarifications_v1 WHERE session_id=? ORDER BY rowid",
        (first.session_id,),
    )
    assert [row["state"] for row in rows] == ["superseded", "resolved"]


async def test_expired_question_does_not_swallow_a_new_topic(clarification_runtime):
    runtime, tasks, _, agent = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    await runtime._session_store.database.execute(
        "UPDATE intent_clarifications_v1 SET expires_at=? WHERE session_id=?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), first.session_id),
    )
    response = await deliver(runtime, "解释一下生成器", first.session_id)
    assert response.content == "ordinary response"
    agent.run.assert_awaited_once()
    assert tasks == []
