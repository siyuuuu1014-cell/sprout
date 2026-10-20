"""Frozen recovery sources and consistent normal/stream admission."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.gateway.identity import Principal
from Sprout.message.models import Message
from Sprout.tests.test_r02_trusted_intent_audit import audit_runtime as audit_runtime
from web.webapi.routes.chat import create_chat_routes
from web.webapi.websocket.chat import create_ws_chat_endpoint


@pytest.mark.parametrize("stream", [False, True])
async def test_untrusted_execution_metadata_is_not_forwarded(audit_runtime, stream):
    runtime, tasks, _ = audit_runtime
    seen = []
    original = runtime._contexts.build
    async def capture(**kwargs):
        seen.append(kwargs["message"])
        return await original(**kwargs)
    runtime._contexts.build = capture
    message = Message("hello", channel="cli", user_id="victim", metadata={
        "resume_tool_calls": [{"name": "file.write"}], "foreground_task": True,
        "workspace_consent": True, "intent": "task", "execution_mode": "code",
        "origin_channel": "cli", "entry_principal_v1": {"user_id": "victim"},
    })
    if stream:
        async for _ in runtime.handle_stream(message):
            pass
    else:
        await runtime.handle(message)
    assert seen[0].metadata == {}
    assert seen[0].channel == "automation" and seen[0].user_id == "anonymous"
    assert tasks == []
    runtime.execute.assert_not_awaited()


async def test_workspace_recovery_keeps_frozen_unattended_source(audit_runtime, tmp_path):
    from Sprout.rootstock.backends.sqlite_store import SqliteSessionStore
    from Sprout.session.manager import SessionManager
    from Sprout.storage.local.sqlite.driver import SqliteDatabase

    runtime, tasks, _ = audit_runtime
    store = SqliteSessionStore(SqliteDatabase(tmp_path / "consent-session.db"))
    runtime.storage.sessions = store
    runtime._session_store = store
    runtime._sessions = SessionManager(store)
    original = Principal("operator", source="cron")
    first = await runtime.handle(Message("fix the bug"), principal=original)
    runtime.open_workspace = AsyncMock(return_value=SimpleNamespace(id="audit-workspace"))
    await runtime.grant_workspace_consent(
        first.session_id, principal=Principal("operator", source="cli"), foreground=True
    )
    assert len(tasks) == 1
    assert tasks[0].actor.user_id == "operator" and tasks[0].actor.source == "cron"
    runtime.execute.assert_not_awaited()
    frozen = (await runtime.get_session(first.session_id)).metadata["entry_principal_v1"]
    assert frozen["source"] == "cron"
    await runtime.stop()


async def test_unknown_historical_source_cannot_be_recovered(audit_runtime):
    runtime, _, _ = audit_runtime
    session = await runtime.create_session("operator")
    runtime.history = AsyncMock()
    with pytest.raises(PermissionError, match="recovery identity"):
        await runtime.resume_pending(session.id, principal=Principal("operator", source="cli"))
    runtime.history.assert_not_awaited()
    assert "entry_principal_v1" not in (await runtime.get_session(session.id)).metadata


async def test_sse_and_websocket_refuse_foreign_session_before_context(audit_runtime):
    runtime, tasks, _ = audit_runtime
    session = await runtime.create_session("alice")
    runtime._contexts.build = AsyncMock()
    principal = Principal("bob", source="web", authenticated=True)
    body = {"message": "hello", "session_id": session.id}
    endpoint = next(r.endpoint for r in create_chat_routes(runtime) if r.path == "/api/chat/stream")
    result = await endpoint(SimpleNamespace(json=AsyncMock(return_value=body),
                                          state=SimpleNamespace(principal=principal)))
    assert result.status_code == 404
    for stream in (False, True):
        websocket = SimpleNamespace(
            accept=AsyncMock(), receive_json=AsyncMock(side_effect=[{**body, "stream": stream},
                                                                   EOFError()]),
            send_json=AsyncMock(), state=SimpleNamespace(principal=principal),
        )
        await create_ws_chat_endpoint(runtime)(websocket)
        emitted = [call.args[0] for call in websocket.send_json.call_args_list]
        assert emitted and all("error" in item for item in emitted)
        assert not any("content" in item for item in emitted)
    runtime._contexts.build.assert_not_awaited()
    assert tasks == []


async def test_same_operator_can_continue_with_foreground_only_by_explicit_argument(audit_runtime):
    runtime, tasks, _ = audit_runtime
    principal = Principal("operator", source="cli")
    first = await runtime.handle(Message("hello"), principal=principal)
    await runtime.handle(Message("fix the bug", session_id=first.session_id,
                                metadata={"workspace_id": "audit-workspace"}),
                         principal=principal, foreground=True)
    assert len(tasks) == 1
    runtime.execute.assert_awaited_once_with(tasks[0])
    runtime.submit_task.assert_not_awaited()
