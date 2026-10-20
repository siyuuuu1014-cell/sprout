"""Cross-record receipts and clarification -> Workspace Consent hand-off."""

import asyncio
from copy import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.message.models import Message
from Sprout.rootstock.backends.sqlite_store import SqliteSessionStore
from Sprout.session.manager import SessionManager
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.tests.test_intent_clarification import (
    AMBIGUOUS,
    OPERATOR,
    deliver,
)
from Sprout.tests.test_intent_clarification import (
    audit_runtime as audit_runtime,
)
from Sprout.tests.test_intent_clarification import (
    clarification_runtime as clarification_runtime,
)

SECOND = "帮我看看缓存模块"


async def selected_write(runtime):
    first = await deliver(runtime, AMBIGUOUS)
    answer = Message("直接修改", session_id=first.session_id, id="fixed-answer-X")
    response = await runtime.handle(answer, principal=OPERATOR)
    assert response.metadata.get("workspace_consent_required") is True
    return first, answer


async def consume_workspace(runtime, session_id):
    runtime.open_workspace.return_value = SimpleNamespace(id="audit-workspace")
    response = await runtime.grant_workspace_consent(session_id, principal=OPERATOR)
    assert response.metadata.get("task_id")


@pytest.mark.parametrize("stream", [False, True])
async def test_pending_workspace_consent_blocks_conflicting_clarification(
    clarification_runtime, stream,
):
    runtime, tasks, events, agent = clarification_runtime
    first, _ = await selected_write(runtime)
    before = await runtime._session_store.get_intent_clarification(first.session_id)
    events.clear()
    response = await deliver(runtime, SECOND, first.session_id, stream=stream)
    assert response.metadata.get("blocked_reason") == "workspace_consent_pending"
    assert not response.metadata.get("clarification_required")
    assert not response.metadata.get("workspace_consent_required")
    assert await runtime._session_store.get_intent_clarification(first.session_id) == before
    assert "工作区" in response.content
    assert not any(event.name == "intent.task.requested" for event in events)
    runtime.open_workspace.assert_not_awaited()
    agent.run.assert_not_awaited()
    assert tasks == []


async def test_A_consent_remains_grantable_after_B_request(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first, _ = await selected_write(runtime)
    await deliver(runtime, SECOND, first.session_id)
    await consume_workspace(runtime, first.session_id)
    assert len(tasks) == 1
    assert AMBIGUOUS in tasks[0].instruction and SECOND not in tasks[0].instruction
    runtime.submit_task.assert_awaited_once()
    runtime.execute.assert_not_awaited()


@pytest.mark.parametrize("same_session", [False, True])
async def test_old_answer_cannot_confirm_new_question(clarification_runtime, same_session):
    runtime, tasks, events, _ = clarification_runtime
    first, answer = await selected_write(runtime)
    await consume_workspace(runtime, first.session_id)
    second = await deliver(runtime, SECOND, first.session_id if same_session else None)
    assert second.metadata.get("clarification_required") is True
    store = runtime._session_store
    before = await store.get_intent_clarification(second.session_id)
    history_before = await runtime.history(second.session_id)
    consent_count = await store.database.scalar("SELECT COUNT(*) FROM workspace_consents_v1")
    runtime.open_workspace.reset_mock()
    runtime.create_task.reset_mock()
    runtime.submit_task.reset_mock()
    events.clear()
    response = await runtime.handle(
        replace(answer, session_id=second.session_id), principal=OPERATOR,
    )
    assert not response.metadata.get("workspace_consent_required")
    assert await store.get_intent_clarification(second.session_id) == before
    assert await runtime.history(second.session_id) == history_before
    assert await store.database.scalar(
        "SELECT COUNT(*) FROM workspace_consents_v1",
    ) == consent_count
    assert not any(event.name == "intent.task.requested" for event in events)
    runtime.open_workspace.assert_not_awaited()
    runtime.create_task.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    assert len(tasks) == 1


async def test_independent_connections_concurrent_old_replay(clarification_runtime, tmp_path):
    runtime, tasks, events, _ = clarification_runtime
    first, answer = await selected_write(runtime)
    await consume_workspace(runtime, first.session_id)
    second = await deliver(runtime, SECOND, first.session_id)
    other = copy(runtime)
    other._session_store = SqliteSessionStore(SqliteDatabase(tmp_path / "conversation.db"))
    other._sessions = SessionManager(other._session_store)
    assert other._session_store.database is not runtime._session_store.database
    before = await runtime._session_store.get_intent_clarification(second.session_id)
    events.clear()
    try:
        responses = await asyncio.gather(
            runtime.handle(answer, principal=OPERATOR),
            other.handle(answer, principal=OPERATOR),
        )
        assert not any(r.metadata.get("workspace_consent_required") for r in responses)
        assert await runtime._session_store.get_intent_clarification(second.session_id) == before
        assert not any(event.name == "intent.task.requested" for event in events)
        assert len(tasks) == 1
    finally:
        other._session_store.close()


async def test_storage_rejects_old_answer_without_python_latest_check(clarification_runtime):
    runtime, _, _, _ = clarification_runtime
    first, answer = await selected_write(runtime)
    await consume_workspace(runtime, first.session_id)
    second = await deliver(runtime, SECOND, first.session_id)
    store = runtime._session_store
    record = await store.get_intent_clarification(second.session_id)
    session = await runtime.get_session(second.session_id)
    assert await store.resolve_intent_clarification(
        session, record["id"], answer.id, answer.content, "write",
    ) is False
    assert (await store.get_intent_clarification(second.session_id))["state"] == "pending"


async def test_message_id_consumption_is_atomic_across_records_and_connections(
    clarification_runtime, tmp_path,
):
    runtime, _, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    second = await deliver(runtime, SECOND)
    store = runtime._session_store
    other = SqliteSessionStore(SqliteDatabase(tmp_path / "conversation.db"))
    try:
        one = await store.get_intent_clarification(first.session_id)
        two = await other.get_intent_clarification(second.session_id)
        results = await asyncio.gather(
            store.resolve_intent_clarification(
                await runtime.get_session(first.session_id), one["id"], "same-message-Y",
                "直接修改", "write",
            ),
            other.resolve_intent_clarification(
                await runtime.get_session(second.session_id), two["id"], "same-message-Y",
                "直接修改", "write",
            ),
        )
        assert results.count(True) == 1 and results.count(False) == 1
    finally:
        other.close()


async def test_resolution_to_consent_crash_gap_blocks_new_question(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    original = runtime._request_workspace
    runtime._request_workspace = AsyncMock(side_effect=OSError("after resolution before Consent"))
    try:
        with pytest.raises(OSError):
            await deliver(runtime, "直接修改", first.session_id)
    finally:
        runtime._request_workspace = original
    response = await deliver(runtime, SECOND, first.session_id)
    assert response.metadata.get("blocked_reason") == "workspace_consent_pending"
    assert not response.metadata.get("clarification_required")
    assert tasks == []


async def test_ordinary_workspace_consent_also_blocks_clarification(clarification_runtime):
    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, "修复登录问题")
    assert first.metadata.get("workspace_consent_required") is True
    response = await deliver(runtime, SECOND, first.session_id)
    assert response.metadata.get("blocked_reason") == "workspace_consent_pending"
    assert await runtime._session_store.get_intent_clarification(first.session_id) is None
    assert tasks == []


async def test_session_body_cleanup_does_not_release_consumed_message_id(clarification_runtime):
    runtime, tasks, events, _ = clarification_runtime
    first, answer = await selected_write(runtime)
    await consume_workspace(runtime, first.session_id)
    await runtime._session_store.delete_session(first.session_id)
    second = await deliver(runtime, SECOND)
    record = await runtime._session_store.get_intent_clarification(second.session_id)
    events.clear()
    response = await runtime.handle(
        replace(answer, session_id=second.session_id), principal=OPERATOR,
    )
    assert not response.metadata.get("workspace_consent_required")
    assert await runtime._session_store.get_intent_clarification(second.session_id) == record
    assert not any(event.name == "intent.task.requested" for event in events)
    assert len(tasks) == 1
