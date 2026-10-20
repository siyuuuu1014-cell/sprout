"""Server-resolved identity boundary; all stores and effects are test doubles."""

from unittest.mock import AsyncMock

import pytest

from Sprout.gateway.identity import Principal
from Sprout.message.models import Message
from Sprout.session.manager import SessionManager
from Sprout.session.models import Session
from Sprout.tests.test_r02_trusted_intent_audit import audit_runtime as audit_runtime


@pytest.mark.parametrize("owner", ["alice", ""])
@pytest.mark.parametrize("caller", [None, Principal("bob", source="test")])
async def test_existing_session_requires_proven_owner(owner, caller):
    store = AsyncMock()
    store.get_session.return_value = Session(id="existing", user_id=owner)
    manager = SessionManager(store)
    with pytest.raises(PermissionError, match="Session unavailable"):
        await manager.resolve(Message("hello", session_id="existing", user_id=owner),
                              principal=caller)
    store.save_session.assert_not_awaited()


async def test_same_operator_can_create_and_continue():
    store = AsyncMock()
    store.get_session.return_value = None
    manager = SessionManager(store)
    caller = Principal("operator", source="test")
    session = await manager.resolve(Message("hello", user_id="forged"), principal=caller)
    assert session.user_id == "operator"
    store.get_session.return_value = session
    assert await manager.resolve(
        Message("again", session_id=session.id), principal=caller
    ) == session
    assert store.save_session.await_count == 1


@pytest.mark.parametrize("entry", ["handle", "handle_stream", "resume_pending",
                                   "resume_pending_task", "grant_workspace_consent"])
async def test_foreign_session_rejected_before_history_context_and_effects(audit_runtime, entry):
    runtime, tasks, _ = audit_runtime
    session = await runtime.create_session("alice")
    runtime._contexts.build = AsyncMock()
    runtime.history = AsyncMock()
    runtime.open_workspace = AsyncMock()
    caller = Principal("bob", source="test")
    with pytest.raises(PermissionError):
        if entry == "handle_stream":
            async for _ in runtime.handle_stream(Message("fix it", session_id=session.id),
                                                  principal=caller):
                pass
        elif entry == "handle":
            await runtime.handle(Message("fix it", session_id=session.id), principal=caller)
        else:
            await getattr(runtime, entry)(session.id, principal=caller)
    runtime._contexts.build.assert_not_awaited()
    runtime.history.assert_not_awaited()
    runtime.open_workspace.assert_not_awaited()
    runtime.create_task.assert_not_awaited()
    assert tasks == []
