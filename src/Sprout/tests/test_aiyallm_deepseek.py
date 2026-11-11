"""Capture actual OpenAI SDK HTTP JSON through installed aiyallm, without credentials."""
import json

import httpx
import pytest

from Sprout.llm.aiyallm import AiyallmProvider
from Sprout.llm.messages import LLMMessage
from Sprout.llm.orchestrator import ModelOrchestrationError, ModelOrchestrator
from Sprout.tools.spec import ToolSpec


def model(monkeypatch, handler, *, name="deepseek", model_id="deepseek-flash", **kwargs):
    original = httpx.AsyncClient
    monkeypatch.setattr("openai.AsyncOpenAI.__init__", _sdk_init(original, handler))
    return AiyallmProvider(model=name + "/" + model_id, base_url="https://example.invalid",
        providers=({"name": name, "type": "openai", "api_key": "fixture-not-a-key",
                    "base_url": "https://example.invalid", "models": [model_id]},), **kwargs)


def _sdk_init(client_type, handler):
    from openai import AsyncOpenAI

    original = AsyncOpenAI.__init__
    def initialize(self, **kwargs):
        kwargs["max_retries"] = 0
        kwargs["http_client"] = client_type(transport=httpx.MockTransport(handler))
        original(self, **kwargs)
    return initialize


def completion(message, *, finish="stop"):
    return {"id": "fixture-response", "object": "chat.completion", "created": 1,
            "model": "deepseek-flash", "choices": [
                {"index": 0, "message": message, "finish_reason": finish}]}


@pytest.mark.parametrize("entry", ["chat", "stream", "stream_events"])
@pytest.mark.parametrize("thinking_mode", [None, "disabled"])
async def test_deepseek_disables_thinking_on_every_wire_path(monkeypatch, entry, thinking_mode):
    captured = []
    def handle(request):
        payload = json.loads(request.content)
        captured.append(payload)
        if entry == "chat":
            return httpx.Response(200, json=completion({"role": "assistant", "content": "OK"}))
        chunk = {"id": "stream", "object": "chat.completion.chunk", "created": 1,
                 "model": "deepseek-flash", "choices": [{"index": 0,
                     "delta": {"content": "OK"}, "finish_reason": "stop"}]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
    provider = model(monkeypatch, handle, thinking_mode=thinking_mode, max_output_tokens=256)
    if entry == "chat":
        assert (await provider.chat([LLMMessage.user("hello")])).content == "OK"
    elif entry == "stream":
        assert "".join([x async for x in provider.stream([LLMMessage.user("hello")])]) == "OK"
    else:
        events = [x async for x in provider.stream_events([LLMMessage.user("hello")])]
        assert "".join(x.content for x in events) == "OK"
    assert captured[0]["thinking"] == {"type": "disabled"}
    assert captured[0]["max_tokens"] == 256
    assert "reasoning_content" not in captured[0]["messages"][0]


async def test_second_tool_request_succeeds_without_fabricated_reasoning(monkeypatch):
    captured = []
    def handle(request):
        payload = json.loads(request.content)
        captured.append(payload)
        if payload.get("thinking") != {"type": "disabled"} and len(captured) > 1:
            return httpx.Response(400, json={"error": {"message":
                "The reasoning_content in the thinking mode must be passed back to the API."}})
        if len(captured) == 1:
            return httpx.Response(200, json=completion({"role": "assistant", "content": None,
                "tool_calls": [{"id": "analysis-call", "type": "function", "function": {
                    "name": "project_analyze", "arguments": '{"path":"src/app.py"}'}}]},
                finish="tool_calls"))
        messages = payload["messages"]
        assert messages[-2]["tool_calls"][0]["id"] == "analysis-call"
        assert messages[-1]["tool_call_id"] == "analysis-call"
        assert messages[-1]["content"] == "python; function greet"
        assert all("reasoning_content" not in message for message in messages)
        return httpx.Response(200, json=completion({
            "role": "assistant", "content": "Analysis done"}))
    provider = model(monkeypatch, handle)
    messages = [LLMMessage.user("Analyze src/app.py")]
    tools = [ToolSpec(name="project_analyze", description="Read-only project structure")]
    response = await provider.chat(messages, tools=tools)
    messages += [LLMMessage.assistant(response.content, response.tool_calls),
                 LLMMessage.tool_result(
                     "analysis-call", "project_analyze", "python; function greet")]
    assert (await provider.chat(messages, tools=tools)).content == "Analysis done"
    assert len(captured) == 2


async def test_other_provider_wire_shape_is_unchanged(monkeypatch):
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=completion({"role": "assistant", "content": "OK"}))
    provider = model(monkeypatch, handle, name="other", model_id="other-model")
    await provider.chat([LLMMessage.user("hello")])
    assert "thinking" not in captured[0]


async def test_bad_request_is_not_retried_by_orchestrator(monkeypatch):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "invalid tool protocol"}})
    provider = model(monkeypatch, handle)
    orchestrator = ModelOrchestrator([provider], max_attempts=3)
    with pytest.raises(ModelOrchestrationError, match="invalid tool protocol"):
        await orchestrator.chat([LLMMessage.user("hello")])
    assert len(calls) == 1
    assert not orchestrator.metrics[0].retryable


def test_factory_forwards_existing_thinking_setting(monkeypatch):
    from Sprout.config.settings import ModelSettings
    from Sprout.runtime.factory import _build_provider

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-not-a-key")
    settings = ModelSettings(thinking_mode="disabled", max_output_tokens=256)
    provider = _build_provider("aiyallm", "deepseek-flash", "https://example.invalid", "", settings)
    assert provider.thinking_mode == "disabled"
    assert provider.max_output_tokens == 256


@pytest.mark.parametrize("status,expected", [(400, 1), (422, 1), (429, 3), (500, 3)])
async def test_retry_classification_uses_nested_http_status(monkeypatch, status, expected):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "fixture failure"}})
    provider = model(monkeypatch, handle)
    with pytest.raises(ModelOrchestrationError):
        await ModelOrchestrator([provider], max_attempts=3).chat([LLMMessage.user("hello")])
    assert len(calls) == expected


def test_no_reasoning_mode_or_invalid_limit_is_silently_accepted():
    for options in ({"thinking_mode": "enabled"}, {"max_output_tokens": 0},
                    {"max_output_tokens": True}):
        with pytest.raises(ValueError):
            AiyallmProvider(model="deepseek-flash", base_url="https://example.invalid", **options)


async def test_default_single_provider_also_sends_disabled_mode(monkeypatch):
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=completion({"role": "assistant", "content": "OK"}))
    monkeypatch.setattr("openai.AsyncOpenAI.__init__", _sdk_init(httpx.AsyncClient, handle))
    provider = AiyallmProvider(model="deepseek-flash", base_url="https://example.invalid",
                              api_key="fixture-not-a-key")
    await provider.chat([LLMMessage.user("hello")])
    assert captured[0]["thinking"] == {"type": "disabled"}


def test_non_openai_provider_spec_is_not_rewritten():
    from Sprout.llm.aiyallm import _configured_provider

    spec = {"type": "anthropic", "name": "claude", "models": ["fixture"],
            "base_url": "https://example.invalid"}
    assert _configured_provider(spec, None) == spec


async def test_host_seeded_tool_result_does_not_require_invented_reasoning(monkeypatch):
    from Sprout.llm.messages import ToolCall

    captured = []
    def handle(request):
        payload = json.loads(request.content)
        captured.append(payload)
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["messages"][-1]["content"] == "greet@1"
        assert not any("reasoning_content" in message for message in payload["messages"])
        return httpx.Response(200, json=completion({
            "role": "assistant", "content": "greet at line 1"}))
    provider = model(monkeypatch, handle)
    messages = [LLMMessage.user("Analyze app.py"),
                LLMMessage.assistant("", (ToolCall("host-read", "project_analyze", {}),)),
                LLMMessage.tool_result("host-read", "project_analyze", "greet@1")]
    assert (await provider.chat(messages)).content == "greet at line 1"
    assert len(captured) == 1
