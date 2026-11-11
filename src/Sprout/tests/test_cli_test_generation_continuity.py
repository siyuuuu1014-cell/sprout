"""Test-generation intent and Session continuation; no Git writes or host execution."""
import json
from unittest.mock import AsyncMock

import pytest

from Sprout.intents import detect_task_hint
from Sprout.message.models import Message
from Sprout.tests.test_intent_clarification import (
    OPERATOR,
    deliver,
)
from Sprout.tests.test_intent_clarification import (
    audit_runtime as audit_runtime,
)
from Sprout.tests.test_intent_clarification import (
    clarification_runtime as clarification_runtime,
)
from Sprout.tests.test_r04_b4_cli_auto import (
    automatic as automatic,
)
from Sprout.tests.test_r04_b4_cli_auto import (
    captured as captured,
)
from Sprout.tests.test_r04_b4_cli_auto import (
    cli as cli,
)
from Sprout.tests.test_r04_b4_cli_auto import (
    rig as rig,
)

PERSISTED_CODING_REQUESTS = (
    (
        "请严格按照 SPEC.md 修复当前项目 order_total.py 中的 c"
        "alculate_order_total 函数。\n"
        "\n"
        "当前已明确授权在 D:\\sprout-order-total-acceptanc"
        "e 工作区执行代码修改。\n"
        "\n"
        "具体要求：\n"
        "\n"
        "1. 首先读取 README.md、SPEC.md、order_total.py"
        "、tests/test_order_total.py 和项目测试配置。\n"
        "2. 确认当前实现与 SPEC.md 的差异。\n"
        "3. 修复 calculate_order_total 对生成器等可迭代输入的支"
        "持问题。\n"
        "4. 不允许通过修改测试或 SPEC.md 来规避实现问题。\n"
        "5. 先执行完整 pytest，保存真实 stdout 和 stderr。\n"
        "6. 如果测试失败，自动分析真实错误并进行一次修复，然后使用完全相同的测试命令重"
        "新执行。\n"
        "7. 测试通过后自动进入 Independent Verification、Re"
        "view 和 Apply。\n"
        "8. Apply 成功后，对实际工作区执行写回后的只读复测。\n"
        "9. 保留完整 Task、Node、Sandbox、测试执行和 Apply 记录"
        "。\n"
        "\n"
        "要求直接执行完整 Coding 工作流，不要退回普通问答模式，不要只提供分析报告"
        "或代码修改建议。\n"
        "\n"
        "如果某个内部工具失败，请明确报告失败的工具、错误原因和当前工作流状态，不要静默回"
        "退到普通 Agent。"
    ),
    (
        "直接执行刚才已经明确的代码修改任务。\n"
        "\n"
        "当前 Workspace 已绑定为 D:\\sprout-order-total-"
        "acceptance，目标文件为 order_total.py。\n"
        "\n"
        "请不要重新询问操作目标，不要再次进行只读分析，也不要退回普通 Agent 对话。"
        "\n"
        "\n"
        "立即创建正式 Coding Task，进入 Git Sandbox，按照已经读取"
        "的 SPEC.md 修复 calculate_order_total 对生成器等"
        "可迭代输入的支持问题。\n"
        "\n"
        "要求：\n"
        "1. 只修改 order_total.py，不修改测试文件和 SPEC.md。\n"
        "2. 运行原始测试命令 python -m pytest -q。\n"
        "3. 如果失败，自动分析错误、修复并使用相同命令重测。\n"
        "4. 测试通过后自动完成 Independent Verification、Re"
        "view、Apply。\n"
        "5. 写回后执行只读复测。\n"
        "6. 全程记录 Task、Node、Sandbox 和 Apply 状态。\n"
        "\n"
        "如果 Coding Task 创建失败，请直接输出底层拒绝原因、失败位置和异常信"
        "息，不要静默回退到普通 Agent。\n"
        "\n"
        "这是已经明确的执行授权，直接开始。"
    ),
)

@pytest.mark.parametrize("text", [
    "为这个项目生成单元测试", "为当前这个项目生成单元测试", "生成单元测试",
    ("修改 order_total.py 中的 calculate_order_total 函数，运行全部测试，"
     "如果测试失败，根据真实错误修复。完成独立 Verification 和 Review 后写回项目。"),
    "为当前这个项目生成单元测试，覆盖 app.py 中的所有函数，并运行测试",
    "为 app.py 创建单元测试", "补充现有 test_app.py 的测试覆盖",
    "那你帮我补齐一份 README", "为这个项目创建 README.md",
    "给当前项目增加 pyproject.toml", "修改 app.py 中的函数",
    "编写测试", "添加测试", "请为这个项目编写测试文件", "给 app.py 添加单元测试",
])
def test_explicit_test_generation_is_task(text):
    assert detect_task_hint(text)


@pytest.mark.parametrize("text", [
    "README 应该写什么？", "如何写单元测试？", "解释 pyproject.toml 的作用。",
    "不要修改 README。", "帮我分析 README。",
    "解释如何为这个项目编写测试", "分析这个项目结构",
    "讨论为当前项目生成单元测试，覆盖 app.py 中的所有函数，并运行测试",
    "不要为当前项目生成单元测试，覆盖 app.py 中的所有函数，并运行测试",
    "解释如何生成单元测试", "讨论测试方案", "生成单元测试的原理是什么",
    "不要为这个项目生成单元测试", "为这个项目生成单元测试是否合理？",
    "生成单元测试的方案", "只分析测试覆盖率", "这个项目有哪些函数", "你好",
])
def test_test_discussion_is_not_a_write_request(text):
    assert not detect_task_hint(text)


@pytest.mark.parametrize("request_text", [
    PERSISTED_CODING_REQUESTS[0],
    PERSISTED_CODING_REQUESTS[1],

    "为这个项目生成单元测试", "那你帮我补齐一份 README",
    "为当前这个项目生成单元测试，覆盖 app.py 中的所有函数，并运行测试",
    "为 app.py 创建单元测试", "补充现有 test_app.py 的测试覆盖",
    ('补充现有 tests/test_order_total.py 的测试覆盖，并运行完整 pytest 测试。'
     '严格依据 SPEC.md，追加两个独立回归：'
     '零价格商品 items=[{"unit_price":0,"quantity":1}] 合法且返回 0.00；'
     '非整数数量 items=[{"unit_price":10,"quantity":1.5}] 必须抛出 ValueError，不能取整。'
     '数量仍为正整数且 bool 无效。保留原有全部20项测试及断言，SPEC.md保持原样，'
     '源码已有正确行为时保持原样。测试命令必须是 python -m pytest -q；'
     '遇到失败自动修复并重测。完成独立 Verification、Review、Apply、写回后只读复测。'),
    ('补充现有 tests/test_order_total.py 的测试覆盖，并运行测试。'
     '零价格商品 unit_price为0且quantity为1时应返回0.00。'),
    '修改 "src/main.py" 中的函数，并运行测试。输入示例为 {"text":"讨论方案，不要修改secret.py"}。',
    '修改 src/main.py 中的函数，并运行测试。参考 SPEC.md 和 README.md，保留规格原样。',
    '参考 SPEC.md 和 README.md，修改 "src/main.py" 中的函数，并运行测试。',
])
async def test_analysis_then_generation_keeps_session_workspace_and_original_request(
    automatic, monkeypatch, request_text, capsys,
):
    cli = automatic
    runtime = cli.rig.runtime
    request = request_text
    cli.state.inputs = ["分析当前项目", request]
    cli.state.answers = [True]
    handled = AsyncMock(wraps=runtime._handle_task)
    monkeypatch.setattr(runtime, "_handle_task", handled)
    await cli.run()
    handled.assert_awaited_once()
    message, session = handled.call_args.args
    assert message.content == request
    assert message.metadata["workspace_id"] == cli.rig.workspace.id
    assert session.id == cli.rig.session.id
    assert cli.create.await_count == 1  # Analysis only.
    output = capsys.readouterr().out
    assert "filesystem_experiment_local_foreground_required" in output
    assert "[Task] creating..." in output


async def test_short_model_options_do_not_create_formal_clarification(clarification_runtime):
    from Sprout.runtime.clarification import route_semantic_clarification

    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, "你好")
    session = await runtime.get_session(first.session_id)
    semantic = {"intent": "task", "semantic": {
        "candidates": [{"intent": "task", "action_mode": "code_modification"}],
        "ambiguity_type": "missing_scope", "missing_information": ["target", "scope"]}}
    reply = await route_semantic_clarification(runtime,
        Message("A", channel="cli", user_id=OPERATOR.user_id,
                session_id=session.id), session, OPERATOR, semantic)
    assert reply is not None and reply.metadata.get("blocked_reason")
    assert await runtime._session_store.get_intent_clarification(session.id) is None
    assert not tasks


async def test_valid_clarification_keeps_original_goal_across_A_and_direct_reply(
    clarification_runtime,
):
    from Sprout.runtime.clarification import _ask

    runtime, tasks, _, _ = clarification_runtime
    first = await deliver(runtime, "你好")
    session = await runtime.get_session(first.session_id)
    original = "为这个项目生成单元测试"
    await _ask(runtime, Message(original, channel="cli", user_id=OPERATOR.user_id,
                                session_id=session.id), session, OPERATOR)
    record = await runtime._session_store.get_intent_clarification(session.id)
    await deliver(runtime, "A", session.id)
    still = await runtime._session_store.get_intent_clarification(session.id)
    assert still["id"] == record["id"] and still["state"] == "pending"
    reply = await deliver(runtime, "直接改", session.id)
    assert reply.metadata.get("workspace_consent_required")
    assert original in reply.content
    settled = await runtime._session_store.get_intent_clarification(session.id)
    assert settled["state"] == "resolved"
    assert json.loads(settled["snapshot_json"])["request"] == original
    assert not tasks


@pytest.mark.parametrize("reply", ["A", "1", "直接改", "直接修改代码"])
async def test_short_reply_without_formal_goal_cannot_start_write(clarification_runtime, reply):
    runtime, tasks, _, agent = clarification_runtime
    first = await deliver(runtime, "你好")
    agent.run.reset_mock()
    response = await deliver(runtime, reply, first.session_id)
    assert response.metadata.get("blocked_reason") == "operation_target_required"
    assert not tasks
    assert await runtime._session_store.get_intent_clarification(first.session_id) is None
    agent.run.assert_not_awaited()


async def test_referential_project_question_uses_verified_analysis_data(automatic):
    from Sprout.runtime.dialogue import project_answer

    cli = automatic
    cli.state.inputs = ["分析当前项目"]
    cli.state.answers = [True]
    await cli.run()
    runtime = cli.rig.runtime
    session = await runtime.get_session(cli.rig.session.id)
    answer = await project_answer(runtime, session, cli.rig.principal)
    assert "Files: 3" in answer and "src/main.py" in answer
    assert "CLI_PROJECT_MAIN_RESULT" not in answer
    context = await runtime._ordinary_conversation_context(
        Message("这个项目有哪些函数", user_id=cli.rig.principal.user_id,
                session_id=session.id), session, {}, {})
    assert context.metadata.get("project_analysis_followup")
    assert any(answer in turn.content for turn in context.memory.working)
    assert context.tools == {}


@pytest.mark.parametrize("entry", ["run", "stream_fallback", "stream_events", "resume"])
@pytest.mark.parametrize("reason", [
    "Diagnostic mode refuses host commands, writes, network",
    "native_project_probe_unavailable", "Tool echo_tool is denied by the security policy",
])
async def test_diagnostic_denial_stops_alternate_tool_loop(entry, reason):
    from Sprout.agent.executor import ActionExecutor
    from Sprout.agent.loop import AgentLoop
    from Sprout.context.context import AgentContext
    from Sprout.llm.messages import LLMResponse, LLMStreamEvent, ToolCall
    from Sprout.session.models import Session
    from Sprout.tests.conftest import EchoTool, ScriptedModel
    from Sprout.tools.executor import ToolExecutor
    from Sprout.tools.registry import ToolRegistry
    from Sprout.tools.result import ToolResult

    class RefusedTool(EchoTool):
        async def invoke(self, arguments):
            self.seen.append(arguments)
            return ToolResult.denied(reason)
    tool = RefusedTool()
    registry = ToolRegistry()
    registry.register(tool)
    call = LLMResponse(tool_calls=(ToolCall("first", "echo_tool", {}),))
    class StreamModel(ScriptedModel):
        async def stream_events(self, messages, *, tools=()):
            response = await self.chat(messages, tools=tools)
            yield LLMStreamEvent(content=response.text, tool_calls=response.tool_calls)
    model_type = StreamModel if entry == "stream_events" else ScriptedModel
    model = model_type([call, call, LLMResponse(content="I know nothing about the project")])
    loop = AgentLoop(model=model, executor=ActionExecutor(ToolExecutor(registry)))
    context = AgentContext(session=Session("test", "owner"), user={"id": "owner"},
                           tools={"echo_tool": tool})
    if entry == "resume":
        context.metadata["resume_tool_calls"] = [{"id": "resume", "name": "echo_tool",
                                                 "arguments": {}}]
    if entry.startswith("stream"):
        chunks = [part async for part in loop.stream(Message("分析项目"), context,
                                                    include_result=True)]
        text = "".join(part if isinstance(part, str) else part.content for part in chunks)
        assert text.count(reason) == 1
    else:
        result = await loop.run(Message("分析项目"), context)
        text = result.content
        assert result.metadata.get("execution_blocked")
        assert result.metadata["tool_calls"] == 1
    assert len(model.calls) == (0 if entry == "resume" else 1)
    assert len(tool.seen) == 1
    assert reason in text
    assert "I know nothing" not in text


async def test_recoverable_tool_error_can_still_continue():
    from Sprout.agent.executor import ActionExecutor
    from Sprout.agent.loop import AgentLoop
    from Sprout.context.context import AgentContext
    from Sprout.llm.messages import LLMResponse, ToolCall
    from Sprout.session.models import Session
    from Sprout.tests.conftest import EchoTool, ScriptedModel
    from Sprout.tools.executor import ToolExecutor
    from Sprout.tools.registry import ToolRegistry
    from Sprout.tools.result import ToolResult

    class RecoverableTool(EchoTool):
        async def invoke(self, arguments):
            self.seen.append(arguments)
            return ToolResult.failure("TimeoutError: timed out")
    tool = RecoverableTool()
    registry = ToolRegistry()
    registry.register(tool)
    model = ScriptedModel([
        LLMResponse(tool_calls=(ToolCall("first", "echo_tool", {}),)),
        LLMResponse(content="The operation timed out; no result was obtained."),
    ])
    loop = AgentLoop(model=model, executor=ActionExecutor(ToolExecutor(registry)))
    result = await loop.run(Message("analyze"), AgentContext(
        session=Session("test", "owner"), user={"id": "owner"}, tools={"echo_tool": tool}))
    assert len(model.calls) == 2
    assert not result.metadata.get("execution_blocked")
    assert "timed out" in result.content


async def test_legacy_resolved_option_cannot_restore_task_A(clarification_runtime):
    from Sprout.agent.base import AgentResult
    from Sprout.runtime.clarification import CLARIFICATION_KEY, clarified_instruction
    from Sprout.session.models import Turn

    runtime, _, _, _ = clarification_runtime
    first = await deliver(runtime, "你好")
    session = await runtime.get_session(first.session_id)
    # Historical store contract could persist a label as a goal. Recreate it
    # with the real store API, not a fabricated authority snapshot.
    turn = await runtime._persist_turns(session,
        Message("A", channel="cli", user_id=OPERATOR.user_id, session_id=session.id),
        AgentResult("Old host question"))
    store = runtime._session_store
    assert await store.issue_intent_clarification(
        session, turn, "Old host question", "2099-01-01T00:00:00+00:00")
    record = await store.get_intent_clarification(session.id)
    answer = Message("直接修改代码", channel="cli", user_id=OPERATOR.user_id,
                     session_id=session.id)
    assert await store.resolve_intent_clarification(
        session, record["id"], answer.id, answer.content, "write")
    held = Turn(session.id, "user", answer.content, metadata={"message_id": answer.id})
    with pytest.raises(LookupError, match="complete original request"):
        await clarified_instruction(runtime, session, held, {CLARIFICATION_KEY: record["id"]})


@pytest.mark.parametrize("stream", [False, True])
async def test_identified_coding_with_unresolved_literal_never_falls_back(
    clarification_runtime, monkeypatch, stream,
):
    runtime, tasks, _, agent = clarification_runtime
    monkeypatch.setattr(runtime, "_classify_intent_with_llm", AsyncMock(return_value={
        "intent": "task", "confidence": 1.0,
    }))
    request = '修改 app.py，输入示例为 {"amount":'
    reply = await deliver(runtime, request, stream=stream, channel="cli")
    assert reply.metadata["blocked_reason"] == "coding_request_not_admitted"
    agent.run.assert_not_awaited()
    assert not tasks


@pytest.mark.parametrize("text", [
    '讨论如何修改 app.py，输入是 {"name":"修改文件"}。',
    '不要修改 app.py，输入是 {"name":"修改文件"}。',
    '分析 "app.py"，数据是 {"instruction":"删除文件"}。',
    '"修改 app.py 并运行测试"',
    '"fix.py"', '"修改app.py"', '"不要修改app.py"',
    '{"instruction":"修改 app.py"}',
    '参考 SPEC.md，讨论如何修改 app.py。',
    '参考 SPEC.md，输入示例为 {"instruction":"修改 app.py"}。',
])
def test_literal_data_cannot_supply_write_instruction(text):
    assert not detect_task_hint(text)


@pytest.mark.parametrize("stream", [False, True])
async def test_json_task_dispatch_matches_handle_and_stream(
    clarification_runtime, stream,
):
    runtime, tasks, _, agent = clarification_runtime
    request = '修改 "app.py" 中的函数，并运行测试。输入为 {"value":1}。'
    reply = await deliver(runtime, request, stream=stream, channel="cli")
    assert reply.metadata.get("workspace_consent_required") is True
    assert not tasks
    agent.run.assert_not_awaited()
    history = await runtime.history(reply.session_id)
    assert next(t.content for t in history if t.role == "user") == request


@pytest.mark.parametrize("stream", [False, True])
async def test_status_question_keeps_conversation_if_classifier_labels_task(
    clarification_runtime, monkeypatch, stream,
):
    runtime, tasks, _, agent = clarification_runtime
    monkeypatch.setattr(runtime, "_classify_intent_with_llm", AsyncMock(return_value={
        "intent": "task", "confidence": 1.0,
    }))
    reply = await deliver(runtime, "好了吗", stream=stream, channel="cli")
    assert not reply.metadata.get("blocked_reason")
    agent.run.assert_awaited_once()
    assert not tasks


@pytest.mark.parametrize("instruction", PERSISTED_CODING_REQUESTS)
def test_persisted_multiline_coding_is_explicit(instruction):
    from Sprout.intents import WriteIntentDisposition, write_intent_disposition

    assert write_intent_disposition(instruction) is WriteIntentDisposition.EXPLICIT_WRITE
    assert detect_task_hint(instruction)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("instruction", PERSISTED_CODING_REQUESTS)
async def test_persisted_multiline_dispatch_matches_handle_and_stream(
    clarification_runtime, instruction, stream,
):
    runtime, tasks, _, agent = clarification_runtime
    reply = await deliver(runtime, instruction, stream=stream, channel="cli")
    assert reply.metadata.get("workspace_consent_required") is True
    agent.run.assert_not_awaited()
    history = await runtime.history(reply.session_id)
    assert next(t.content for t in history if t.role == "user") == instruction
    assert not tasks


@pytest.mark.parametrize("instruction", [
    "不要修改任何文件。\n1. 修复 order_total.py。\n2. 运行测试。",
    "只分析当前项目。\n具体讨论修改 order_total.py 的方案。",
    "如果需要修改代码。\n修复 order_total.py。",
    "修复 order_total.py。\n取消本次任务，不要执行。",
    "修复 order_total.py。\n请不要修改任何文件。",
    "修复 order_total.py。\nDo not modify any files.",
    "修复 order_total.py。\nOnly analyze the project.",
    "直接执行刚才的代码修改任务。\n当前已经授权修改任何文件。",
])
def test_multiline_constraints_and_claims_cannot_supply_edit(instruction):
    assert not detect_task_hint(instruction)


@pytest.mark.parametrize("stream", [False, True])
async def test_conflicting_multiline_coding_reports_reason_without_tool_agent(
    clarification_runtime, monkeypatch, stream,
):
    runtime, tasks, _, agent = clarification_runtime
    monkeypatch.setattr(runtime, "_classify_intent_with_llm", AsyncMock(return_value={
        "intent": "task", "confidence": 1.0,
    }))
    reply = await deliver(runtime, "修复 order_total.py。\n不要修改任何文件。",
                          stream=stream, channel="cli")
    assert reply.metadata.get("blocked_reason") == "coding_request_not_admitted"
    assert reply.metadata.get("reason") == "conflicting_constraints"
    agent.run.assert_not_awaited()
    assert not tasks
