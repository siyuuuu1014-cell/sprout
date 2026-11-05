"""Normal stream completion reports existing counts once, without repeating tokens."""
import asyncio
from unittest.mock import Mock

import pytest

from Sprout.agent.base import AgentResult
from Sprout.agent.executor import ActionExecutor
from Sprout.agent.loop import AgentLoop
from Sprout.context.context import AgentContext
from Sprout.llm.messages import LLMResponse, LLMStreamEvent, ToolCall
from Sprout.message.models import Message
from Sprout.session.models import Session
from Sprout.tests.conftest import EchoTool
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry


@pytest.mark.parametrize("events", [False, True])
@pytest.mark.parametrize("calls", [0, 1, 2])
async def test_normal_stream_terminal_counts_actual_calls(monkeypatch, events, calls):
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: Mock())
    registry = ToolRegistry()
    tool = EchoTool()
    registry.register(tool)
    class Model:
        count = 0
        async def chat(self, messages, **kwargs):
            self.count += 1
            return (LLMResponse(tool_calls=(ToolCall(id=str(self.count), name="echo_tool",
                                                    arguments={"text": "safe"}),))
                    if self.count <= calls else LLMResponse(content="answer", finish_reason="stop"))
    class EventModel(Model):
        async def stream_events(self, messages, **kwargs):
            response = await self.chat(messages, **kwargs)
            if response.tool_calls:
                yield LLMStreamEvent(tool_calls=response.tool_calls, finish_reason="tool_calls")
            else:
                yield LLMStreamEvent(content="ans")
                yield LLMStreamEvent(content="wer", finish_reason="stop")
    agent = AgentLoop(EventModel() if events else Model(),
                      executor=ActionExecutor(ToolExecutor(registry)), max_steps=4)
    context = AgentContext(Session("stream", "owner"), tools=registry.list())
    chunks = [part async for part in agent.stream(
        Message(content="hello"), context, include_result=True)]
    terminals = [part for part in chunks if isinstance(part, AgentResult)]
    assert "".join(part for part in chunks if isinstance(part, str)) == "answer"
    assert len(terminals) == 1 and terminals[0].content == ""
    assert terminals[0].metadata["tool_calls"] == calls == len(tool.seen)
    assert terminals[0].metadata["model_calls"] == calls + 1


@pytest.mark.parametrize("failure", ["exception", "cancel", "truncated", "budget"])
async def test_incomplete_stream_does_not_emit_normal_terminal(monkeypatch, failure):
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: Mock())
    class Model:
        async def stream_events(self, messages, **kwargs):
            yield LLMStreamEvent(content="partial")
            if failure == "exception":
                raise RuntimeError("controlled failure")
            if failure == "cancel":
                raise asyncio.CancelledError()
            yield LLMStreamEvent(finish_reason="length")
    agent = AgentLoop(Model(), max_steps=0 if failure == "budget" else 4)
    chunks = []
    async def consume():
        async for part in agent.stream(Message(content="hi"), AgentContext(Session("s", "u")),
                                       include_result=True):
            chunks.append(part)
    if failure in {"exception", "cancel"}:
        with pytest.raises(RuntimeError if failure == "exception" else asyncio.CancelledError):
            await consume()
    else:
        await consume()
    assert not any(isinstance(part, AgentResult) for part in chunks)
