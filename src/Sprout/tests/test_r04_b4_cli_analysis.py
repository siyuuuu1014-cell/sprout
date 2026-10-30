"""Actual REPL -> R02 -> Task -> Guard -> tools -> model -> CLI.

Terminal input, OS identity and secure-open use controlled substitutes.
"""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.agent.executor import ActionExecutor
from Sprout.agent.loop import AgentLoop
from Sprout.cli.commands import chat
from Sprout.llm.messages import LLMResponse, ToolCall
from Sprout.message.models import Message
from Sprout.task.models import TaskExecutionMode, TaskStatus
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.fixture
async def cli(rig, captured, monkeypatch):
    from Sprout.runtime.intent_analysis import response_example

    state = SimpleNamespace(
        terminal=False, classification=0, analysis=True, seen=[], prompts=[],
        answers=["src/main.py", True], inputs=["帮我分析当前项目的代码架构。"],
        display=[], lives=[], session=rig.session.id, user="DISPLAY_NOT_IDENTITY",
        workspace_id=rig.workspace.id, intents=[], query=None,
    )
    class Model:
        name = "cli-analysis-controlled"

        async def chat(self, messages, **kwargs):
            if "Classify the user's intent" in str(messages[0].content):
                state.classification += 1
                intent = state.intents.pop(0) if state.intents else (
                    "workspace" if state.analysis else "conversation")
                action = "read_only_analysis" if intent == "workspace" else "conversation"
                value = json.loads(response_example(True))
                value.update(intent=intent, confidence=0.99)
                value["semantic"].update(
                    action_mode=action, candidates=[{"intent": intent, "action_mode": action}],
                    ambiguity_type="none", missing_information=[],
                    needs_clarification=False, question="")
                return LLMResponse(content=json.dumps(value))
            state.seen.extend(messages)
            if state.query:
                operation, state.query = state.query, None
                return LLMResponse(content="", tool_calls=[ToolCall(
                    id="cli-query", name="workspace_query", arguments={
                        "workspace_id": rig.workspace.id, "operation": operation,
                        "name": "main", "path": "src/main.py"})])
            return LLMResponse(content="CLI_PROJECT_MAIN_RESULT")

    model = Model()
    rig.runtime.models.register(model, default=True)
    rig.runtime.register_agent("cli-controlled", AgentLoop(
        model, executor=ActionExecutor(rig.executor)), default=True)
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: Mock())
    monkeypatch.setattr(chat, "current_cli_principal", lambda: rig.principal)
    monkeypatch.setattr(chat.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    claim = AsyncMock(wraps=rig.commission)
    create = AsyncMock(wraps=rig.runtime.create_task)
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", claim)
    monkeypatch.setattr(rig.runtime, "create_task", create)

    async def readline():
        if not state.inputs:
            raise EOFError
        return state.inputs.pop(0)
    monkeypatch.setattr(chat, "_readline_with_slash_hint", readline)

    def prompt(text, **kwargs):
        state.prompts.append(str(text))
        class Prompt:
            async def ask_async(self):
                assert state.classification == 1, "prompt before R02 or duplicate classification"
                assert not any(live.active for live in state.lives), "Live owns terminal"
                answer = state.answers.pop(0)
                if isinstance(answer, BaseException):
                    raise answer
                if callable(answer):
                    return await answer()
                return answer
        return Prompt()
    monkeypatch.setattr(chat.questionary, "text", prompt)
    monkeypatch.setattr(chat.questionary, "confirm", prompt)

    class Console:
        def __init__(self, **kwargs):
            self.is_terminal = state.terminal

        def print(self, *args, **kwargs):
            state.display.extend(str(x) for x in args)

    class Live:
        def __init__(self, *args, **kwargs):
            self.active = False
            self.transitions = []
            state.lives.append(self)

        def __enter__(self):
            self.start()
            return self

        def __exit__(self, *args):
            self.stop()

        def start(self, **kwargs):
            assert not self.active
            self.active = True
            self.transitions.append("start")

        def stop(self):
            assert self.active
            self.active = False
            self.transitions.append("stop")

        def update(self, value):
            assert self.active
            state.display.append(value.markup)

    monkeypatch.setattr(chat, "Console", Console)
    monkeypatch.setattr(chat, "Live", Live)
    monkeypatch.setattr(chat, "_render_cli_markdown", lambda text: state.display.append(text))
    for name in ("_print_reply_meta", "_print_session_separator"):
        monkeypatch.setattr(chat, name, Mock())
    for name in ("_handle_cli_workspace_consent", "_handle_cli_approval_prompt"):
        monkeypatch.setattr(chat, name, AsyncMock(return_value=False))
    monkeypatch.setattr(chat, "_report_parked_tasks", AsyncMock())

    async def run():
        await chat._repl(None, session=state.session, user=state.user,
                         model=SimpleNamespace(provider="controlled", model="stub"),
                         runtime=rig.runtime, web=SimpleNamespace(),
                         workspace_id=state.workspace_id)
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("first_session", [False, True])
async def test_real_repl_single_file_business_roundtrip(cli, terminal, first_session):
    cli.state.terminal = terminal
    cli.state.session = None if first_session else cli.rig.session.id
    await cli.run()
    assert cli.state.classification == 1
    assert len(cli.state.prompts) == 2
    cli.create.assert_awaited_once()
    kwargs = cli.create.call_args.kwargs
    assert kwargs["actor"] == cli.rig.principal
    assert kwargs["actor"].user_id != cli.state.user
    assert kwargs["execution_mode"] is TaskExecutionMode.READ_ONLY
    assert kwargs["delegation_scope"].allowed_paths == (str(cli.rig.root / "src/main.py"),)
    assert kwargs["delegation_scope"].allowed_actions == frozenset({"tool.invoke", "file.read"})
    assert not kwargs["delegation_scope"].permits("file.read", str(cli.rig.root / "src"))
    assert kwargs["metadata"]["required_paths"] == ["src/main.py"]
    session_id = kwargs["metadata"]["session_id"]
    session = await cli.rig.runtime._session_store.get_session(session_id)
    assert session.user_id == cli.rig.principal.user_id
    tasks = await cli.rig.storage.metadata.list_tasks()
    task = next(task for task in tasks if task.id != cli.rig.task.id)
    assert task.status is TaskStatus.COMPLETED
    nodes = await cli.rig.storage.metadata.list_execution_nodes(task.id)
    assert len(nodes) == 1 and nodes[0].metadata["execution_guard"]["state"] == "completed"
    cli.claim.assert_awaited_once()
    assert cli.captured.opens == ["src/main.py"]
    assert any(str(m.role) == "tool" and "main" in m.content for m in cli.state.seen)
    assert "CLI_PROJECT_MAIN_RESULT" in cli.state.display
    if terminal:
        assert cli.state.lives[0].transitions == ["start", "stop", "start", "stop"]


@pytest.mark.parametrize("stream", [False, True])
async def test_runtime_provider_called_once_after_classification(cli, stream):
    provider = AsyncMock(return_value=None)
    message = Message("请分析项目结构", user_id="display", session_id=cli.rig.session.id)
    if stream:
        chunks = [chunk async for chunk in cli.rig.runtime.handle_stream(
            message, principal=cli.rig.principal, analysis_task_provider=provider)]
        result = next(chunk.outbound for chunk in chunks if chunk.outbound)
    else:
        result = await cli.rig.runtime.handle(
            message, principal=cli.rig.principal, analysis_task_provider=provider)
    assert result.metadata["analysis_status"] == "unavailable"
    provider.assert_awaited_once()
    assert provider.call_args.kwargs["session"].id == cli.rig.session.id
    assert provider.call_args.kwargs["principal"] == cli.rig.principal
    assert cli.state.classification == 1
    cli.create.assert_not_called()
    assert not cli.captured.opens


@pytest.mark.parametrize("answers,reason", [
    ([""], "project_analysis_cancelled"),
    ([None], "project_analysis_cancelled"),
    (["src/main.py", False], "project_analysis_cancelled"),
    ([KeyboardInterrupt()], "project_analysis_cancelled"),
    ([EOFError()], "project_analysis_cancelled"),
    (["../outside.py"], "analysis_file_selection_invalid"),
    (["/outside.py"], "analysis_file_selection_invalid"),
    (["C:/outside.py"], "analysis_file_selection_invalid"),
    (["src/main.py\nsrc/worker.py"], "project_multi_file_analysis_unavailable"),
    (["auto"], "project_auto_discovery_unavailable"),
    (["*"], "project_auto_discovery_unavailable"),
])
async def test_repl_refusal_never_creates_task_or_reads(cli, answers, reason):
    cli.state.answers = answers
    cli.state.terminal = True
    await cli.run()
    assert reason in cli.state.display
    cli.create.assert_not_called()
    cli.claim.assert_not_called()
    assert not cli.captured.opens and not cli.state.seen
    assert cli.state.classification == 1
    assert cli.state.lives[0].transitions == ["start", "stop", "start", "stop"]


async def test_repl_ordinary_chat_has_no_analysis_prompt(cli):
    cli.state.analysis = False
    cli.state.inputs = ["你好，今天怎么样"]
    await cli.run()
    assert cli.state.classification == 1 and cli.state.seen
    assert not cli.state.prompts and not cli.captured.opens
    cli.create.assert_not_called()
    cli.claim.assert_not_called()


@pytest.mark.parametrize("command", ["/help", "请切换到英文"])
async def test_existing_cli_commands_do_not_enter_new_route(cli, monkeypatch, command):
    cli.state.inputs = [command]
    monkeypatch.setattr(chat, "_show_slash_directory", Mock())
    monkeypatch.setattr(chat, "_apply_language_request", Mock())
    await cli.run()
    assert cli.state.classification == 0 and not cli.state.prompts
    cli.create.assert_not_called()
    assert not cli.captured.opens


@pytest.mark.parametrize("workspace", ["", "not-registered"])
async def test_repl_needs_actual_registered_workspace(cli, workspace):
    cli.state.workspace_id = workspace
    await cli.run()
    assert "cli_analysis_workspace_required" in cli.state.display
    assert not cli.state.prompts and not cli.state.seen
    cli.create.assert_not_called()
    assert not cli.captured.opens


async def test_repl_policy_deny_does_not_treat_confirmation_as_grant(cli, monkeypatch):
    from copy import deepcopy

    from Sprout.tests.test_r04_pa2_b1_binding import Record

    before = deepcopy(await cli.rig.storage.operational.list_approvals())
    policy = Record("deny")
    monkeypatch.setattr(cli.rig.runtime, "_policy_engine", policy)
    monkeypatch.setattr(cli.rig.executor, "policy_engine", policy)
    await cli.run()
    cli.create.assert_awaited_once()
    cli.claim.assert_not_called()
    assert not cli.captured.opens and not cli.state.seen
    assert "analysis_task_resource_unavailable" in cli.state.display
    assert await cli.rig.storage.operational.list_approvals() == before


async def test_repl_does_not_fabricate_authenticated_os_identity(cli, monkeypatch, capsys):
    monkeypatch.setattr(chat, "current_cli_principal", Mock(side_effect=ValueError("SID denied")))
    await cli.run()
    assert "cli_analysis_identity_unavailable" in capsys.readouterr().out
    assert cli.state.classification == 0 and not cli.state.prompts
    cli.create.assert_not_called()
    assert not cli.captured.opens


async def test_repl_unauthenticated_identity_never_prompts_or_creates(cli, monkeypatch):
    monkeypatch.setattr(chat, "current_cli_principal", lambda: replace(
        cli.rig.principal, authenticated=False))
    await cli.run()
    assert "analysis_task_subject_unavailable" in cli.state.display
    assert not cli.state.prompts
    cli.create.assert_not_called()
    assert not cli.captured.opens


async def test_repl_requires_interactive_confirmation(cli, monkeypatch):
    monkeypatch.setattr(chat.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    await cli.run()
    assert "cli_analysis_interactive_confirmation_required" in cli.state.display
    assert not cli.state.prompts
    cli.create.assert_not_called()
    assert not cli.captured.opens


async def test_repl_session_continues_without_reclassifying_first_message(cli):
    cli.state.session = None
    cli.state.inputs.append("你好，继续聊天")
    cli.state.intents = ["workspace", "conversation"]
    await cli.run()
    assert cli.state.classification == 2 and len(cli.state.prompts) == 2
    # Existing ordinary-stream persistence is background work, unlike the
    # completed analysis dispatch. Observe it through its real lifecycle.
    await asyncio.gather(*tuple(cli.rig.runtime._background_tasks))
    cli.create.assert_awaited_once()
    session_id = cli.create.call_args.kwargs["metadata"]["session_id"]
    turns = await cli.rig.runtime._session_store.recent_turns(session_id, limit=20)
    assert len(turns) == 4
    assert sum(turn.content == "帮我分析当前项目的代码架构。" for turn in turns) == 1
    assert any(turn.content == "你好，继续聊天" for turn in turns)
    assert cli.captured.opens == ["src/main.py"]


async def test_provider_gets_detached_session_not_storage_authority(cli):
    async def provider(**request):
        assert not request["message"].metadata
        request["session"].user_id = "forged"
        request["session"].metadata.clear()
        return None
    result = await cli.rig.runtime.handle(
        Message("请分析项目结构", session_id=cli.rig.session.id),
        principal=cli.rig.principal, analysis_task_provider=provider)
    assert result.metadata["reason"] == "project_analysis_cancelled"
    session = await cli.rig.runtime._session_store.get_session(cli.rig.session.id)
    assert session.user_id == cli.rig.principal.user_id
    assert session.metadata["entry_principal_v1"]["authenticated"] is True
    assert not cli.captured.opens


async def test_model_metadata_cannot_supply_provider(cli):
    result = await cli.rig.runtime.handle(
        Message("请分析项目结构", session_id=cli.rig.session.id,
                metadata={"analysis_task_provider": "trusted", "trusted": True}),
        principal=cli.rig.principal)
    assert result.metadata["reason"] == "authorized_analysis_task_required"
    assert not cli.state.prompts and not cli.captured.opens
    cli.create.assert_not_called()


@pytest.mark.parametrize("operation", ["symbols", "neighbors", "subgraph",
                                       "dependencies", "knowledge"])
async def test_repl_query_tool_result_reaches_model_and_terminal(cli, operation):
    cli.state.query = operation
    await cli.run()
    cli.create.assert_awaited_once()
    cli.claim.assert_awaited_once()
    queries = [m for m in cli.state.seen if str(m.role) == "tool" and m.tool_call_id == "cli-query"]
    assert len(queries) == 1 and "Tool error" not in queries[0].content
    assert "CLI_PROJECT_MAIN_RESULT" in cli.state.display
    assert cli.captured.opens == ["src/main.py", "src/main.py"]


async def test_explicit_task_takes_precedence_over_provider(cli):
    task = await cli.rig.runtime.create_task(
        cli.rig.workspace.id, "analyze", actor=cli.rig.principal, source="cli",
        execution_mode=TaskExecutionMode.READ_ONLY,
        delegation_scope=replace(cli.rig.task.delegation_scope,
                                 allowed_paths=(str(cli.rig.root / "src/main.py"),)),
        metadata={"session_id": cli.rig.session.id, "required_paths": ["src/main.py"]})
    provider = AsyncMock(side_effect=AssertionError("unneeded second Task provider"))
    result = await cli.rig.runtime.handle(
        Message("请分析项目结构", session_id=cli.rig.session.id),
        principal=cli.rig.principal, analysis_task=task, analysis_task_provider=provider)
    assert result.metadata["analysis_status"] == "ok"
    provider.assert_not_called()
    cli.create.assert_awaited_once()


async def test_provider_reference_still_requires_authoritative_task(cli):
    provider = AsyncMock(return_value=replace(cli.rig.task, id="forged-missing-task"))
    result = await cli.rig.runtime.handle(
        Message("请分析项目结构", session_id=cli.rig.session.id),
        principal=cli.rig.principal, analysis_task_provider=provider)
    assert result.metadata["analysis_status"] == "unavailable"
    provider.assert_awaited_once()
    assert not cli.state.seen and not cli.captured.opens
    cli.claim.assert_not_called()


async def test_session_authority_changed_during_confirmation_blocks_task_creation(cli):
    async def change_session():
        session = await cli.rig.runtime._session_store.get_session(cli.rig.session.id)
        session.metadata["entry_principal_v1"]["source"] = "web"
        await cli.rig.runtime._session_store.save_session(session)
        return True
    cli.state.answers = ["src/main.py", change_session]
    await cli.run()
    assert "cli_analysis_identity_unavailable" in cli.state.display
    cli.create.assert_not_called()
    assert not cli.state.seen and not cli.captured.opens


async def test_legacy_session_without_identity_does_not_invoke_provider(cli):
    session = await cli.rig.runtime._session_store.get_session(cli.rig.session.id)
    await cli.rig.runtime._session_store.save_session(replace(session, metadata={}))
    provider = AsyncMock(side_effect=AssertionError("legacy identity promoted"))
    result = await cli.rig.runtime.handle(
        Message("请分析项目结构", session_id=cli.rig.session.id),
        principal=cli.rig.principal, analysis_task_provider=provider)
    assert result.metadata["analysis_status"] == "unavailable"
    provider.assert_not_called()
    assert not cli.captured.opens


async def test_real_rich_live_pauses_and_restarts_around_controlled_prompt(cli):
    from io import StringIO

    from rich.console import Console
    from rich.live import Live

    cli.state.classification = 1
    cli.state.answers = [None]
    with Live("loading", console=Console(file=StringIO(), force_terminal=True),
              auto_refresh=False) as live:
        original = chat._provide_cli_analysis_task

        async def check_paused(*args, **kwargs):
            assert not live.is_started
            return await original(*args, **kwargs)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(chat, "_provide_cli_analysis_task", check_paused)
            provider = chat._cli_analysis_task_provider(cli.rig.runtime, cli.rig.workspace.id, live)
            result = await provider(message=Message("analyze project"),
                                    session=cli.rig.session, principal=cli.rig.principal)
        assert result is None and live.is_started
    cli.create.assert_not_called()
