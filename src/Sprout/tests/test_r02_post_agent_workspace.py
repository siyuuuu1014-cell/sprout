"""Post-agent workspace requests cannot authorize known non-writing input."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.gateway.identity import Principal
from Sprout.intents import WriteIntentDisposition, detect_task_hint, write_intent_disposition
from Sprout.message.models import Message
from Sprout.runtime.runtime import WORKSPACE_CONSENT_KEY
from Sprout.session.models import Turn
from Sprout.tests.test_r02_status_narrative import COMMAND_TEXTS, STATUS_TEXTS
from Sprout.tests.test_r02_trusted_intent_audit import audit_runtime as audit_runtime
from Sprout.tests.test_workspace_consent import (
    CODING_REQUEST_TYPO,
    _AsksForWorkspace,
    _repo,
    _runtime_with_agent,
)

CORE_STATUS = "修复登录问题的任务目前仍未完成"
NONWRITES = (
    "修复登录问题仍在进行中",
    "fix the login bug task remains pending",
    "昨天已经修复了登录问题",
    "不要修改文件，只读分析",
    "Please review the fix without editing files.",
    "实现缓存的任务此前完成了",
    "这里引用“修复登录问题”，不是让你执行",
    "implementation of the cache layer",
)
UNKNOWNS = (
    CODING_REQUEST_TYPO,
    "帮我给当前项目添上一份按价格排序的程序",
    "Could you put a CSV exporter in this project?",
)


async def deliver(runtime, text, stream):
    message = Message(text, metadata={"intent": "task", "workspace_consent": True})
    if stream:
        chunks = [chunk async for chunk in runtime.handle_stream(message)]
        if runtime._background_tasks:
            await asyncio.gather(*list(runtime._background_tasks))
        return next(chunk.outbound for chunk in chunks if chunk.outbound is not None)
    return await runtime.handle(message, principal=Principal("u-1", source="cli"))


def guard_effects(runtime):
    runtime._request_workspace = AsyncMock(wraps=runtime._request_workspace)
    runtime.create_task = AsyncMock()
    runtime.submit_task = AsyncMock()
    runtime.execute = AsyncMock()
    runtime.open_workspace = AsyncMock()


# Preserve the original independent probe's real AgentLoop/tool and all refusal
# assertions. Only its placement/fixture plumbing changes for repository CI.
@pytest.mark.parametrize("stream", [False, True])
def test_agent_workspace_signal_does_not_turn_status_into_consent(tmp_path, stream):
    async def scenario():
        runtime, model = _runtime_with_agent(tmp_path, _AsksForWorkspace())
        guard_effects(runtime)
        assert detect_task_hint(CORE_STATUS) is False
        try:
            await deliver(runtime, CORE_STATUS, stream)
            print(json.dumps({"stream": stream, "hint": False,
                              "scripted_model_requested_workspace": model.asked,
                              "consent_requests": runtime._request_workspace.await_count,
                              "tasks_created": runtime.create_task.await_count,
                              "submit_calls": runtime.submit_task.await_count,
                              "execute_calls": runtime.execute.await_count}))
            assert model.asked is True
            runtime.create_task.assert_not_awaited()
            runtime.submit_task.assert_not_awaited()
            runtime.execute.assert_not_awaited()
            runtime.open_workspace.assert_not_awaited()
            runtime._request_workspace.assert_not_awaited()
        finally:
            await runtime.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("classification", ["task", "conversation"])
@pytest.mark.parametrize("text", NONWRITES, ids=[f"nonwrite-{i}" for i in range(8)])
async def test_real_model_tool_cannot_reopen_nonwrite_consent(
    tmp_path, stream, classification, text,
):
    runtime, model = _runtime_with_agent(tmp_path, _AsksForWorkspace(classify_as=classification))
    guard_effects(runtime)
    try:
        response = await deliver(runtime, text, stream)
        assert model.asked is True  # The actual scripted tool call is not hidden.
        assert not response.metadata.get(WORKSPACE_CONSENT_KEY)
        runtime._request_workspace.assert_not_awaited()
        runtime.open_workspace.assert_not_awaited()
        runtime.create_task.assert_not_awaited()
        runtime.submit_task.assert_not_awaited()
        runtime.execute.assert_not_awaited()
    finally:
        await runtime.stop()


@pytest.mark.parametrize("text", STATUS_TEXTS)
def test_known_nonwrite_disposition_is_not_unknown(text):
    assert write_intent_disposition(text) is WriteIntentDisposition.KNOWN_NONWRITE
    assert detect_task_hint(text) is False


@pytest.mark.parametrize("text", ["", "  "])
def test_empty_input_cannot_mint_workspace_consent(text):
    assert write_intent_disposition(text) is WriteIntentDisposition.KNOWN_NONWRITE
    assert detect_task_hint(text) is False


@pytest.mark.parametrize("text", COMMAND_TEXTS)
def test_explicit_write_disposition_keeps_the_bool_api(text):
    assert write_intent_disposition(text) is WriteIntentDisposition.EXPLICIT_WRITE
    assert detect_task_hint(text) is True


@pytest.mark.parametrize("text", (*UNKNOWNS, "hello"))
def test_unknown_is_not_a_negative_or_write_authorization(text):
    assert write_intent_disposition(text) is WriteIntentDisposition.UNKNOWN
    assert detect_task_hint(text) is False


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("text", (CORE_STATUS, *NONWRITES[:4]), ids=[f"held-{i}" for i in range(5)])
async def test_historical_nonwrite_hold_is_refused_before_any_mutation(audit_runtime, text, bound):
    runtime, saved, _ = audit_runtime
    original = Principal("operator", source="cron")
    first = await runtime.handle(Message("hello"), principal=original)
    session = await runtime.get_session(first.session_id)
    if bound:
        session.metadata["workspace_id"] = "previous-workspace"
        await runtime._session_store.save_session(session)
    before = deepcopy(session.metadata)
    await runtime._session_store.append_turn(Turn(
        session_id=session.id, role="user", content=text,
        metadata={"message_id": "historical-held", "message_metadata": {
            WORKSPACE_CONSENT_KEY: True, "proposed_workspace_root": "/unused",
            "intent": "task", "origin_channel": "cli", "workspace_consent": True,
        }},
    ))
    runtime.open_workspace = AsyncMock(return_value=SimpleNamespace(id="audit-workspace"))
    runtime._remember_session_workspace = AsyncMock(wraps=runtime._remember_session_workspace)
    consumed = set(runtime._consumed_consents)
    history_before = await runtime.history(session.id)
    with pytest.raises(LookupError, match="not a coding request"):
        await runtime.grant_workspace_consent(
            session.id, principal=Principal("operator", source="cli"), user_id="forged"
        )
    runtime.open_workspace.assert_not_awaited()
    runtime._remember_session_workspace.assert_not_awaited()
    runtime.create_task.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    runtime.execute.assert_not_awaited()
    assert saved == [] and runtime._consumed_consents == consumed
    assert (await runtime.get_session(session.id)).metadata == before
    assert await runtime.history(session.id) == history_before


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("text", UNKNOWNS, ids=["typo", "unfamiliar", "english-question"])
async def test_unknown_needs_explicit_original_instruction_confirmation(tmp_path, text, stream):
    runtime, model = _runtime_with_agent(tmp_path, _AsksForWorkspace())
    runtime.create_task = AsyncMock(wraps=runtime.create_task)
    runtime.open_workspace = AsyncMock(wraps=runtime.open_workspace)
    runtime.submit_task = AsyncMock()
    runtime.start_worker = Mock()
    runtime.execute = AsyncMock()
    try:
        response = await deliver(runtime, text, stream)
        assert detect_task_hint(text) is False and model.asked is True
        assert response.metadata.get(WORKSPACE_CONSENT_KEY) is True
        assert text in response.content and "明确确认" in response.content
        runtime.open_workspace.assert_not_awaited()
        runtime.create_task.assert_not_awaited()
        runtime.submit_task.assert_not_awaited()
        runtime.execute.assert_not_awaited()
        assert runtime._consumed_consents == set()
        # Only this separate trusted-human action confirms the saved original;
        # caller label/source cannot replace it or upgrade its frozen identity.
        result = await runtime.grant_workspace_consent(
            response.session_id, root=_repo(tmp_path), user_id="forged",
            principal=Principal("u-1", source="web"),
        )
        tasks = await runtime.list_tasks()
        assert len(tasks) == 1 and tasks[0].instruction == text
        assert tasks[0].actor.user_id == "u-1" and tasks[0].actor.source == "cli"
        assert result.metadata["task_id"] == tasks[0].id
        runtime.create_task.assert_awaited_once()
        runtime.open_workspace.assert_awaited_once()
        runtime.submit_task.assert_awaited_once_with(tasks[0])
        runtime.execute.assert_not_awaited()
        with pytest.raises(LookupError):
            await runtime.grant_workspace_consent(response.session_id)
        assert len(await runtime.list_tasks()) == 1
        assert runtime.create_task.await_count == 1 and runtime.submit_task.await_count == 1
    finally:
        await runtime.stop()
