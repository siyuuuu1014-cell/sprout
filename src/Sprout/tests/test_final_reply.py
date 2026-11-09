import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from Sprout.agent.base import AgentResult
from Sprout.tests.test_aiyallm_deepseek import model
from Sprout.tests.test_r04_b4_cli_auto import automatic as automatic
from Sprout.tests.test_r04_b4_cli_auto import captured as captured
from Sprout.tests.test_r04_b4_cli_auto import cli as cli
from Sprout.tests.test_r04_b4_cli_auto import rig as rig


async def test_aiyallm_retains_content_finish_and_usage_without_tool_calls(monkeypatch):
    from Sprout.llm.messages import LLMMessage

    def handle(request):
        chunks = [dict(choices=[dict(index=0, delta={'content': text}, finish_reason=finish)],
                       id='fixture', object='chat.completion.chunk', created=1,
                       model='deepseek-flash')
                  for text, finish in [('first', None), ('LAST', 'length')]]
        chunks.append(dict(id='fixture', object='chat.completion.chunk', created=1,
                           model='deepseek-flash', choices=[],
                           usage={'prompt_tokens': 1, 'completion_tokens': 8, 'total_tokens': 9}))
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            text=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n')
    provider = model(monkeypatch, handle, max_output_tokens=8)
    events = [e async for e in provider.stream_events([LLMMessage.user('hi')])]
    assert ''.join(e.content for e in events) == 'firstLAST'
    assert any(e.finish_reason == 'length' for e in events)
    assert events[-1].usage.completion_tokens == 8
    assert events[-1].model in {"deepseek-flash", provider.model}


async def test_agent_node_preserves_summary_beyond_4000_characters(tmp_path):
    from Sprout.orchestration.models import ExecutionNode, NodeType
    from Sprout.runtime.nodes import NodeExecutor
    from Sprout.task.models import Task
    from Sprout.workspace.models import Workspace

    text = ('This is a complete paragraph.\n' * 200) + 'FINAL_SENTENCE_END.'
    executor = NodeExecutor(
        metadata=SimpleNamespace(list_execution_nodes=AsyncMock(return_value=[])),
        router=Mock(), contexts=Mock(), tools=Mock(), skills=Mock(), read_broker=None,
        file_broker=None, process_broker=None, apply_broker=None)
    executor._check_model_publication = AsyncMock()
    executor._model_access = Mock(return_value=([], []))
    executor._publish_node_model = AsyncMock(return_value=AgentResult(content=text))
    result = await executor._agent(ExecutionNode(type=NodeType.AGENT),
                                  Workspace('workspace', tmp_path), Task())
    assert result['content'] == text


@pytest.mark.parametrize('terminal', [False, True])
async def test_repl_renders_full_reply_after_last_chunk_and_task_event(cli, monkeypatch, terminal):
    from Sprout.cli.commands import chat
    from Sprout.message.models import OutboundMessage, StreamChunk

    cli.state.terminal = terminal
    cli.state.inputs = ['hello']
    text = 'paragraph\n' * 100 + 'FINAL_SENTENCE_END.'
    consumed = []
    async def stream(*args, **kwargs):
        yield StreamChunk(content=text[:300])
        yield StreamChunk(outbound=OutboundMessage(
            'task completed', 'cli', cli.rig.session.id, 'fixture',
                                                  metadata={'task_status': 'completed'}))
        yield StreamChunk(content=text[300:])
        consumed.append(True)
    monkeypatch.setattr(chat, '_cli_turn_stream', stream)
    rendered = Mock(side_effect=lambda value: consumed and value)
    monkeypatch.setattr(chat, '_render_cli_markdown', rendered)
    await cli.run()
    assert consumed == [True]
    rendered.assert_called_once_with(text)


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('finish', ['stop', 'length'])
async def test_agent_preserves_tool_final_reply_and_marks_length(monkeypatch, streaming, finish):
    from Sprout.agent.executor import ActionExecutor
    from Sprout.agent.loop import AgentLoop
    from Sprout.context.context import AgentContext
    from Sprout.llm.messages import LLMResponse, LLMStreamEvent, ToolCall, Usage
    from Sprout.message.models import Message
    from Sprout.session.models import Session
    from Sprout.tests.conftest import EchoTool
    from Sprout.tools.executor import ToolExecutor
    from Sprout.tools.registry import ToolRegistry

    monkeypatch.setattr('Sprout.agent.loop.get_agent_step_store', lambda: Mock())
    text = 'A long complete summary.\n' * 200 + 'FINAL_MARKER'
    class Model:
        calls = 0

        async def chat(self, messages, **kwargs):
            assert "cli_tool_run" not in messages[0].content
            assert [tool.name for tool in kwargs["tools"]] == ["echo_tool"]
            self.calls += 1
            return (LLMResponse(tool_calls=(ToolCall('call', 'echo_tool', {'text': 'done'}),))
                    if self.calls == 1 else LLMResponse(content=text, finish_reason=finish,
                                                       model="fixture-model",
                                                       usage=Usage(completion_tokens=128)))
    class Streaming(Model):
        async def stream_events(self, messages, **kwargs):
            response = await self.chat(messages, **kwargs)
            if response.tool_calls:
                yield LLMStreamEvent(tool_calls=response.tool_calls, finish_reason='tool_calls')
            else:
                yield LLMStreamEvent(content=text[:300])
                yield LLMStreamEvent(content=text[300:])
                yield LLMStreamEvent(finish_reason=finish, usage=response.usage,
                                     model=response.model)
    registry = ToolRegistry()
    registry.register(EchoTool())
    agent = AgentLoop(Streaming() if streaming else Model(),
                      executor=ActionExecutor(ToolExecutor(registry)))
    context = AgentContext(Session('session', 'owner'), tools=registry.list())
    if streaming:
        chunks = [part async for part in agent.stream(
            Message('hello'), context, include_result=True)]
        result = next(part for part in chunks if isinstance(part, AgentResult))
        content = ''.join(part for part in chunks if isinstance(part, str))
    else:
        result = await agent.run(Message('hello'), context)
        content = result.content
    assert content.startswith(text)
    assert result.metadata['finish_reason'] == finish
    assert result.metadata['model'] == 'fixture-model'
    assert result.metadata['truncated'] is (finish == 'length')
    if finish == 'length':
        assert 'incomplete' in content or '尚未完成' in content
    assert agent.model.calls == 2


@pytest.mark.parametrize('streaming', [False, True])
async def test_openai_compatible_keeps_finish_and_usage(monkeypatch, streaming):
    from Sprout.llm.messages import LLMMessage
    from Sprout.llm.openai_compatible import OpenAICompatibleProvider

    original = httpx.AsyncClient
    def handle(request):
        body = json.loads(request.content)
        assert body['max_tokens'] == 8
        if not body.get('stream'):
            return httpx.Response(200, json={'choices': [{'message': {'content': 'ALL'},
                'finish_reason': 'length'}], 'usage': {'completion_tokens': 8}})
        chunks = [{'choices': [{'delta': {'content': 'A'}, 'finish_reason': None}]},
                  {'choices': [{'delta': {'content': 'LL'}, 'finish_reason': 'length'}]},
                  {'choices': [], 'usage': {'completion_tokens': 8}}]
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            text=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(handle), **kwargs))
    provider = OpenAICompatibleProvider(model='fixture', base_url='https://example.invalid',
                                        api_key='fixture', max_output_tokens=8)
    if streaming:
        events = [event async for event in provider.stream_events([LLMMessage.user('hi')])]
        assert ''.join(event.content for event in events) == 'ALL'
        assert events[-1].finish_reason == 'length'
        assert events[-1].usage.completion_tokens == 8
    else:
        response = await provider.chat([LLMMessage.user('hi')])
        assert response.text == 'ALL'
    assert provider.last_response_info == {
        'finish_reason': 'length', 'completion_tokens': 8, 'max_tokens': 8}


@pytest.mark.parametrize('terminal', [False, True])
async def test_repl_keeps_received_content_when_stream_fails(cli, monkeypatch, terminal):
    from Sprout.cli.commands import chat
    from Sprout.message.models import StreamChunk

    cli.state.terminal = terminal
    cli.state.inputs = ['hello']

    async def stream(*args, **kwargs):
        yield StreamChunk(content='Already received.')
        yield StreamChunk(error='Provider connection closed')

    monkeypatch.setattr(chat, '_cli_turn_stream', stream)
    render = Mock()
    monkeypatch.setattr(chat, '_render_cli_markdown', render)
    await cli.run()
    render.assert_called_once_with('Already received.')
