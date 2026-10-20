"""A selection cannot silently transfer a different target onto the frozen goal."""

import json
from types import SimpleNamespace

import pytest

from Sprout.config.loader import settings_from_dict
from Sprout.runtime.intent_analysis import parse_analysis
from Sprout.tests.test_intent_clarification import audit_runtime as audit_runtime
from Sprout.tests.test_intent_clarification import clarification_runtime as clarification_runtime
from Sprout.tests.test_intent_clarification import deliver
from Sprout.tests.test_intent_semantic_analysis import model, report


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("original,answer", [
    ("帮我看看登录功能", "请修复缓存模块"),
    ("帮我看看登录功能", "只分析缓存模块"),
    ("帮我看看订单接口", "请修复支付接口"),
    ("帮我看看订单接口", "只分析支付接口"),
    ("帮我看看报表模块", "可以直接修改调度模块的代码"),
    ("帮我看看报表模块", "先把调度问题的原因分析给我"),
    ("take a look at the parser module", "please fix the lexer module"),
    ("take a look at the parser module", "only analyze the lexer module"),
])
async def test_different_target_is_not_a_selection_of_original(
    clarification_runtime, stream, original, answer,
):
    runtime, tasks, _, agent = clarification_runtime
    first = await deliver(runtime, original, stream=stream)
    assert first.metadata.get("clarification_required")
    store = runtime._session_store
    record = await store.get_intent_clarification(first.session_id)
    response = await deliver(runtime, answer, first.session_id, stream=stream)
    after = await store.get_intent_clarification(first.session_id, record_id=record["id"])
    assert after["state"] != "resolved"
    assert not response.metadata.get("clarification_resolution")
    if response.metadata.get("workspace_consent_required"):
        assert not response.metadata.get("intent_clarification_v1")
        held, metadata = await runtime._held_workspace_consent(first.session_id)
        assert held.content == answer
        assert not metadata.get("intent_clarification_v1")
    if agent.run.await_count:
        assert agent.run.call_args.args[0].content == answer
    runtime.create_task.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    runtime.open_workspace.assert_not_awaited()
    assert not tasks


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("original,answer,choice", [
    ("帮我看看登录功能", "可以直接修改登录模块的代码", "write"),
    ("帮我看看登录功能", "先把登录问题的原因分析给我", "read"),
    ("帮我看看订单接口", "可以直接修改订单模块的代码", "write"),
    ("帮我看看订单接口", "先把订单问题的原因分析给我", "read"),
    ("take a look at the parser module", "please go ahead and modify the parser module", "write"),
    ("take a look at the parser module", "only analyze the parser module", "read"),
])
async def test_same_explicit_target_can_select_frozen_goal(
    clarification_runtime, stream, original, answer, choice,
):
    runtime, tasks, _, agent = clarification_runtime
    first = await deliver(runtime, original, stream=stream)
    response = await deliver(runtime, answer, first.session_id, stream=stream)
    record = await runtime._session_store.get_intent_clarification(first.session_id)
    assert record["state"] == "resolved" and record["resolution"] == choice
    assert bool(response.metadata.get("workspace_consent_required")) == (choice == "write")
    assert json.loads(record["snapshot_json"])["request"] == original
    agent.run.assert_not_awaited()
    runtime.open_workspace.assert_not_awaited()
    assert not tasks


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("answer", [
    "可以直接修改那个服务的实现", "我希望你能修改另外一个模块",
    "I would like you to modify another component",
])
async def test_uncertain_target_cannot_confirm_original_even_with_model_write(
    clarification_runtime, stream, answer,
):
    runtime, tasks, events, _ = clarification_runtime
    first = await deliver(runtime, "帮我看看登录功能", stream=stream)
    model(runtime, report(ambiguous=False, choice="write", evidence=answer))
    events.clear()
    reply = await deliver(runtime, answer, first.session_id, stream=stream)
    record = await runtime._session_store.get_intent_clarification(first.session_id)
    assert record["state"] != "resolved"
    assert not reply.metadata.get("workspace_consent_required")
    assert not any(e.name == "intent.task.requested" for e in events)
    assert not tasks


@pytest.mark.parametrize("enabled", [False, True])
async def test_system_and_user_prompt_agree_on_response_protocol(clarification_runtime, enabled):
    runtime, _, _, _ = clarification_runtime
    runtime._settings = settings_from_dict({"runtime": {"intent_semantics_enabled": enabled}})
    fake = model(runtime, report(ambiguous=False))
    await deliver(runtime, "你好")
    messages = fake.chat.call_args.args[0]
    system, user = messages[0].content, messages[-1].content
    instruction = user.split("Input:\n", 1)[0]
    example = instruction.split("Return JSON like ", 1)[-1].strip().rstrip(".")
    expected = {"intent", "confidence", "reason", "semantic"} if enabled else {
        "intent", "confidence", "reason",
    }
    assert set(json.loads(example)) == expected
    assert ("semantic" in system) is enabled
    if enabled:
        assert parse_analysis(example) is not None
        assert "four top-level fields" in system
    else:
        assert "three top-level fields" in system


@pytest.mark.parametrize("stream", [False, True])
async def test_new_target_consent_creates_only_new_instruction_after_explicit_grant(
    clarification_runtime, stream,
):
    from Sprout.tests.test_intent_clarification import OPERATOR

    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, "帮我看看登录功能", stream=stream)
    response = await deliver(runtime, "请修复缓存模块", first.session_id, stream=stream)
    assert response.metadata.get("workspace_consent_required")
    assert tasks == []
    runtime.open_workspace.return_value = SimpleNamespace(id="audit-workspace")
    await runtime.grant_workspace_consent(first.session_id, principal=OPERATOR)
    assert len(tasks) == 1
    assert tasks[0].instruction == "请修复缓存模块"
    runtime.submit_task.assert_awaited_once()
    runtime.execute.assert_not_awaited()
