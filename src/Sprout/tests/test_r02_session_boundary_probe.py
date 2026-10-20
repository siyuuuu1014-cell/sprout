"""R02 stop-condition probe; no production edits, models or external effects.

Base: dev@3a532341. The original audit file remains unchanged (SHA256
1B000FA341F6CB99836743D9517FD669147F13D4C88F55946BFB31D5C72C0DB2).
The injected Principal represents an already resolved server identity; this
tests the authenticated route-to-Runtime boundary, not token validation.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from Sprout.agent.base import AgentResult
from Sprout.gateway.identity import Principal
from Sprout.session.models import Turn
from Sprout.tests.test_r02_trusted_intent_audit import audit_runtime as audit_runtime
from web.webapi.routes.chat import create_chat_routes


async def test_authenticated_web_caller_cannot_read_or_append_foreign_session(audit_runtime):
    runtime, saved_tasks, _ = audit_runtime
    marker = "TEST-ONLY-ALICE-PRIVATE-HISTORY"
    session = await runtime.create_session("alice")
    await runtime._session_store.append_turn(
        Turn(session_id=session.id, role="user", content=marker))
    before = await runtime.history(session.id)
    observed = {"foreign_history_visible": False}

    async def answer(message, context):
        observed["foreign_history_visible"] = any(
            getattr(turn, "content", None) == marker for turn in context.memory)
        return AgentResult("test-only fixed reply; no tools or model")

    agent = SimpleNamespace(run=AsyncMock(side_effect=answer))
    runtime.register_agent("session-probe", agent, default=True)
    runtime.handle = AsyncMock(wraps=runtime.handle)
    endpoint = next(route.endpoint for route in create_chat_routes(runtime)
                    if route.path == "/api/chat")
    response = await endpoint(SimpleNamespace(
        json=AsyncMock(return_value={
            "message": "Hello", "session_id": session.id, "user_id": "alice"}),
        state=SimpleNamespace(principal=Principal(
            user_id="bob", authenticated=True, source="web"))))

    after = await runtime.history(session.id)
    forwarded = runtime.handle.call_args.args[0]
    # Prove the identity adapter honored Bob, not the forged body user_id.
    assert forwarded.user_id == "bob" and session.user_id == "alice"
    assert saved_tasks == []
    runtime.execute.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    print(json.dumps({
        "http_status": response.status_code,
        "server_identity_differs_from_session_owner": True,
        "agent_invocations": agent.run.await_count,
        "foreign_history_visible": observed["foreign_history_visible"],
        "foreign_session_appended": len(after) > len(before),
        "tasks_created": len(saved_tasks),
    }))
    assert response.status_code in {403, 404}
    agent.run.assert_not_awaited()
    assert len(after) == len(before)
