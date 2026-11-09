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


@pytest.mark.parametrize("text", [
    "为这个项目生成单元测试", "为当前这个项目生成单元测试", "生成单元测试",
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
    "为这个项目生成单元测试", "那你帮我补齐一份 README",
    "为当前这个项目生成单元测试，覆盖 app.py 中的所有函数，并运行测试",
    "为 app.py 创建单元测试", "补充现有 test_app.py 的测试覆盖",
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
    assert "filesystem_experiment_local_foreground_required" in capsys.readouterr().out


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
    assert await project_answer(runtime, session, cli.rig.principal) == "CLI_PROJECT_MAIN_RESULT"
    context = await runtime._ordinary_conversation_context(
        Message("这个项目有哪些函数", user_id=cli.rig.principal.user_id,
                session_id=session.id), session, {}, {})
    assert context.metadata.get("project_analysis_followup")
    assert any("CLI_PROJECT_MAIN_RESULT" in turn.content for turn in context.memory.working)
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
