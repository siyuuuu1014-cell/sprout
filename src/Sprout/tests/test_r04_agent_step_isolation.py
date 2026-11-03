"""Real Runtime/AgentLoop and SQLite recording; controlled model, no network."""

import asyncio
import json
import sqlite3
from uuid import uuid4

import pytest

from Sprout.agent import step_store
from Sprout.gateway.identity import Principal
from Sprout.llm.messages import LLMResponse, LLMStreamEvent, ToolCall, Usage
from Sprout.message.models import Message
from Sprout.session.models import Session
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task
from Sprout.tests.conftest import EchoTool, ScriptedModel, build_runtime
from Sprout.tools.registry import ToolRegistry
from Sprout.workspace.models import Workspace


def rows(path):
    with sqlite3.connect(path) as conn:
        return conn.execute(
            'SELECT session_id,content,tool_calls_json,prompt_tokens,'
            'completion_tokens,total_tokens FROM agent_steps ORDER BY id').fetchall()


@pytest.mark.parametrize("streaming", [False, True, "structured"])
async def test_two_runtime_step_stores_are_isolated(tmp_path, monkeypatch, streaming):
    default_path = tmp_path / 'default.db'
    default = step_store.AgentStepStore(default_path)
    monkeypatch.setattr(step_store, '_store', default)
    contexts = []
    for name in ('a', 'b'):
        path = tmp_path / (name + '.db')
        bundle = StorageBundle.in_memory()
        bundle.metadata = open_metadata_store(path)
        tools = ToolRegistry()
        tools.register(EchoTool())
        model = ScriptedModel([
            LLMResponse(tool_calls=(ToolCall(name, 'echo_tool', {'text': name}),),
                        usage=Usage(prompt_tokens=11, completion_tokens=3, total_tokens=14)),
            LLMResponse(content='answer-' + name,
                        usage=Usage(prompt_tokens=17, completion_tokens=5, total_tokens=22)),
        ])
        if streaming == "structured":
            async def events(messages, *, tools=(), provider=model):
                response = await provider.chat(messages, tools=tools)
                yield LLMStreamEvent(content=response.content or '',
                                     tool_calls=response.tool_calls, usage=response.usage,
                                     finish_reason='stop')
            model.stream_events = events
        runtime, _ = build_runtime(storage=bundle, model=model, tools=tools)
        principal = Principal(name, source='cli')
        session = Session(id=str(uuid4()), user_id=name)
        await runtime._session_store.save_session(session)
        workspace = Workspace(id=str(uuid4()), root=tmp_path / name)
        await bundle.metadata.save_workspace(workspace)
        task = Task(workspace_id=workspace.id, actor=principal,
                    metadata={'session_id': session.id})
        await bundle.metadata.save_task(task)
        await runtime.start()
        contexts.append((runtime, path, principal, session, task, name))

    async def run(context):
        runtime, _, principal, session, _, name = context
        message = Message('hello', user_id=name, channel='cli', session_id=session.id)
        if streaming:
            chunks = []
            async for chunk in runtime.handle_stream(message, principal=principal):
                assert step_store.get_agent_step_store() is default
                chunks.append(chunk)
            assert any('answer-' + name in c.content for c in chunks)
        else:
            result = await runtime.handle(message, principal=principal)
            assert result.content == 'answer-' + name

    try:
        await asyncio.gather(*(run(context) for context in contexts))
        evidence = []
        for _runtime, path, _, session, task, name in contexts:
            actual = rows(path)
            assert len(actual) == 2
            assert {row[0] for row in actual} == {session.id}
            assert actual[1][1] == 'answer-' + name
            assert json.loads(actual[0][2]) == [
                {'id': name, 'name': 'echo_tool', 'arguments': {'text': name}}]
            assert [row[3:] for row in actual] == [(11, 3, 14), (17, 5, 22)]
            evidence.append({'path': str(path), 'task': task.id, 'session': session.id,
                             'rows': actual})
        assert rows(default_path) == []
        (tmp_path / 'ISOLATION_EVIDENCE.json').write_text(json.dumps(evidence, indent=2))
    finally:
        for runtime, path, *_ in contexts:
            await runtime.stop()
            await runtime.stop()  # Closing borrowed recording must be idempotent.
            assert SqliteDatabase.shared_connection_count(path) == 0
        default.close()


def test_legacy_default_reopens_after_close(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.db'
    monkeypatch.setattr(step_store, '_DEFAULT_PATH', path)
    monkeypatch.setattr(step_store, '_store', None)
    first = step_store.get_agent_step_store()
    first.close()
    second = step_store.get_agent_step_store()
    try:
        assert second is not first
        second.record(session_id='legacy', step=1, content='ok', tool_calls=[],
                      prompt_tokens=1, completion_tokens=2, total_tokens=3)
        assert rows(path)[0][1] == 'ok'
    finally:
        second.close()


async def test_new_runtime_reopens_configured_database(tmp_path, monkeypatch):
    default = step_store.AgentStepStore(tmp_path / 'default.db')
    monkeypatch.setattr(step_store, '_store', default)
    path = tmp_path / 'reopened.db'
    recorders = []
    try:
        for generation in range(2):
            bundle = StorageBundle.in_memory()
            bundle.metadata = open_metadata_store(path)
            runtime, _ = build_runtime(storage=bundle)
            await runtime.start()
            try:
                response = await runtime.handle(Message('hello', user_id='owner'),
                                               principal=Principal('owner', source='cli'))
                assert response.content == 'Echo: hello'
                recorders.append(runtime._agent_step_store)
                assert len(rows(path)) == generation + 1
            finally:
                await runtime.stop()
            assert recorders[-1]._closed
            assert SqliteDatabase.shared_connection_count(path) == 0
        assert recorders[0] is not recorders[1]
        assert rows(default._path) == []
    finally:
        default.close()


async def test_memory_runtime_never_uses_default_file(tmp_path, monkeypatch):
    default = step_store.AgentStepStore(tmp_path / 'default.db')
    monkeypatch.setattr(step_store, '_store', default)
    runtime, _ = build_runtime()
    try:
        await runtime.start()
        await runtime.handle(Message('hello'), principal=Principal('owner', source='cli'))
        assert runtime._agent_step_store._db is not default._db
        assert rows(default._path) == []
    finally:
        await runtime.stop()
        default.close()
