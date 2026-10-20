"""Short chat replies are not answers without a live clarification question."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from Sprout.agent.base import AgentResult
from Sprout.message.models import Message
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

REPLIES = ("好的", "是的", "可以", "取消", "只分析", "直接修改")
STATES = ("new", "ordinary", "resolved", "cancelled", "expired_pending", "expired", "superseded")


async def prepare(runtime, state):
    if state == "new":
        return None
    if state == "ordinary":
        return (await deliver(runtime, "你好")).session_id
    first = await deliver(runtime, AMBIGUOUS)
    if state == "resolved":
        await deliver(runtime, "只分析", first.session_id)
    elif state == "cancelled":
        await deliver(runtime, "取消", first.session_id)
    elif state == "superseded":
        await deliver(runtime, "解释一下生成器", first.session_id)
    else:
        await runtime._session_store.database.execute(
            "UPDATE intent_clarifications_v1 SET expires_at=? WHERE session_id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), first.session_id),
        )
        if state == "expired":
            await deliver(runtime, "解释一下生成器", first.session_id)
    return first.session_id


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("text", REPLIES)
@pytest.mark.parametrize("stream", [False, True])
async def test_no_live_question_preserves_chat_and_blocks_targetless_write(
    clarification_runtime, state, text, stream,
):
    runtime, tasks, _, agent = clarification_runtime
    session_id = await prepare(runtime, state)
    before = await runtime.history(session_id) if session_id else []
    record_before = (await runtime._session_store.get_intent_clarification(session_id)
                     if session_id else None)
    agent.run.reset_mock()
    response = await deliver(runtime, text, session_id, stream=stream)
    assert not response.metadata.get("clarification_required")
    assert not response.metadata.get("workspace_consent_required")
    assert "没有可回答的待澄清请求" not in response.content
    if text == "直接修改":
        assert response.metadata.get("blocked_reason") == "operation_target_required"
        assert "对象" in response.content
        agent.run.assert_not_awaited()
    else:
        assert response.content == "ordinary response"
        agent.run.assert_awaited_once()
    runtime.open_workspace.assert_not_awaited()
    runtime.create_task.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    runtime.execute.assert_not_awaited()
    assert tasks == []
    history = await runtime.history(response.session_id)
    assert len(history) == len(before) + 2
    assert [turn.content for turn in history[-2:]] == [text, response.content]
    record_after = await runtime._session_store.get_intent_clarification(response.session_id)
    if state == "expired_pending":
        assert record_after["state"] == "expired" and record_after["resolution"] == "expired"
    else:
        assert record_after == record_before
    assert await runtime._session_store.database.scalar(
        "SELECT COUNT(*) FROM workspace_consents_v1"
    ) == 0


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("text", ["好的", "是的", "可以"])
async def test_live_question_still_requires_specific_selection(clarification_runtime, stream, text):
    runtime, tasks, _, agent = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    response = await deliver(runtime, text, first.session_id, stream=stream)
    assert response.metadata.get("clarification_required") is True
    assert response.metadata["clarification_id"] == first.metadata["clarification_id"]
    assert (await runtime._session_store.get_intent_clarification(first.session_id))["state"] == (
        "pending"
    )
    agent.run.assert_not_awaited()
    assert tasks == []


@pytest.mark.parametrize("text", ["直接修改", "修改代码", "直接修改代码", "直接修复", "fix it"])
@pytest.mark.parametrize("bound", [False, True])
async def test_isolated_write_reply_cannot_use_model_or_existing_workspace(
    clarification_runtime, text, bound,
):
    runtime, tasks, events, agent = clarification_runtime
    first = await deliver(runtime, "你好")
    if bound:
        session = await runtime.get_session(first.session_id)
        session.metadata["workspace_id"] = "audit-workspace"
        await runtime._session_store.save_session(session)
    agent.run.reset_mock()
    agent.run.return_value = AgentResult("", metadata={"workspace_requested": True})
    runtime._classify_intent_with_llm = AsyncMock(return_value={"intent": "task", "confidence": 1})
    events.clear()
    response = await deliver(runtime, text, first.session_id, metadata={
        "confirmed": True, "intent": "task", "workspace_id": "audit-workspace",
    })
    assert response.metadata.get("blocked_reason") == "operation_target_required"
    runtime._classify_intent_with_llm.assert_not_awaited()
    agent.run.assert_not_awaited()
    runtime.open_workspace.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    assert not any(event.name == "intent.task.requested" for event in events)
    assert tasks == []


@pytest.mark.parametrize("answer", ["只分析", "取消", "直接修改"])
async def test_exact_answer_replay_remains_idempotent(clarification_runtime, answer):
    runtime, tasks, _, agent = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    message = Message(answer, session_id=first.session_id)
    await runtime.handle(message, principal=OPERATOR)
    history = await runtime.history(first.session_id)
    record = await runtime._session_store.get_intent_clarification(first.session_id)
    agent.run.reset_mock()
    response = await runtime.handle(message, principal=OPERATOR)
    assert "已经处理" in response.content
    assert await runtime.history(first.session_id) == history
    assert await runtime._session_store.get_intent_clarification(first.session_id) == record
    agent.run.assert_not_awaited()
    assert tasks == []


async def test_readonly_response_discloses_that_no_repository_was_inspected(clarification_runtime):
    runtime, tasks, _, agent = clarification_runtime
    first = await deliver(runtime, AMBIGUOUS)
    response = await deliver(runtime, "只分析", first.session_id)
    assert "未读取或检查真实仓库文件" in response.content
    assert response.metadata.get("analysis_scope") == "request_text_only"
    assert response.metadata.get("repository_inspected") is False
    runtime.open_workspace.assert_not_awaited()
    agent.run.assert_not_awaited()
    assert tasks == []


@pytest.mark.parametrize("text", REPLIES)
async def test_memory_backend_keeps_ordinary_short_chat(audit_runtime, text):
    runtime, tasks, _ = audit_runtime
    response = await deliver(runtime, text)
    if text == "直接修改":
        assert response.metadata.get("blocked_reason") == "operation_target_required"
    else:
        assert response.content == "audit: no tools or models"
    assert not response.metadata.get("clarification_required")
    assert not response.metadata.get("workspace_consent_required")
    runtime.create_task.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    assert tasks == []
