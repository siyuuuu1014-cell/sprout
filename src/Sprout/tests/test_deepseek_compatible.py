import json

import httpx
import pytest

from Sprout.config.loader import settings_from_dict
from Sprout.llm.messages import LLMMessage, Role
from Sprout.llm.openai_compatible import OpenAICompatibleProvider
from Sprout.runtime.factory import _build_provider
from Sprout.tools.spec import ToolSpec


def provider(**kwargs):
    return OpenAICompatibleProvider(model="deepseek-flash", base_url="https://api.deepseek.com",
        api_key="fixture-not-a-key", thinking_mode="disabled", max_output_tokens=256, **kwargs)


def mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr("Sprout.llm.openai_compatible.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.parametrize("entry", ["chat", "stream", "stream_events"])
async def test_all_request_paths_send_explicit_mode_and_output_limit(monkeypatch, entry):
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        if entry == "chat":
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
        chunk = {"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")

    mock_client(monkeypatch, handle)
    model = provider()
    messages = [LLMMessage.user("Reply OK")]
    if entry == "chat":
        assert (await model.chat(messages)).content == "OK"
    elif entry == "stream":
        assert "".join([part async for part in model.stream(messages)]) == "OK"
    else:
        chunks = [part async for part in model.stream_events(messages)]
        assert any(part.content == "OK" for part in chunks)
    assert captured[0]["thinking"] == {"type": "disabled"}
    assert captured[0]["max_tokens"] == 256
    assert captured[0].get("stream", False) == (entry != "chat")


async def test_tool_result_round_trip_keeps_call_identity(monkeypatch):
    captured = []

    def handle(request):
        payload = json.loads(request.content)
        captured.append(payload)
        if len(captured) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": None,
                "tool_calls": [{"id": "fixture-call", "type": "function", "function": {
                    "name": "add", "arguments": '{"a":2,"b":3}'}}]},
                "finish_reason": "tool_calls"}]})
        assert payload["messages"][-2]["tool_calls"][0]["id"] == "fixture-call"
        assert payload["messages"][-1]["tool_call_id"] == "fixture-call"
        assert payload["messages"][-1]["content"] == "5"
        return httpx.Response(200, json={"choices": [{"message": {"content": "5"}}]})

    mock_client(monkeypatch, handle)
    model = provider()
    tool = ToolSpec(name="add", description="Add integers", input_schema={"type": "object"})
    messages = [LLMMessage.user("Add 2 and 3")]
    response = await model.chat(messages, tools=[tool])
    assert response.tool_calls[0].arguments == {"a": 2, "b": 3}
    messages += [LLMMessage(role=Role.ASSISTANT, tool_calls=response.tool_calls),
                 LLMMessage.tool_result("fixture-call", "add", "5")]
    assert (await model.chat(messages, tools=[tool])).content == "5"
    assert all(item["thinking"] == {"type": "disabled"} for item in captured)


def test_unconfigured_providers_keep_previous_wire_shape():
    model = OpenAICompatibleProvider(model="fixture", base_url="https://example.invalid/v1",
                                     api_key="fixture-not-a-key")
    assert model._request_payload([LLMMessage.user("hello")], ()) == {
        "model": "fixture", "messages": [{"role": "user", "content": "hello"}]}


@pytest.mark.parametrize("limit", [0, -1, True, "256"])
def test_invalid_output_limits_are_rejected(limit):
    with pytest.raises(ValueError, match="positive integer"):
        OpenAICompatibleProvider(model="fixture", base_url="https://example.invalid",
                                 max_output_tokens=limit)


def test_enabled_thinking_is_not_silently_advertised_as_supported():
    with pytest.raises(ValueError, match="reasoning round-trip"):
        OpenAICompatibleProvider(model="fixture", base_url="https://example.invalid",
                                 thinking_mode="enabled")


def test_toml_model_fields_reach_provider_without_real_key(monkeypatch):
    monkeypatch.setenv("SPROUT_FIXTURE_MODEL_KEY", "fixture-not-a-key")
    settings = settings_from_dict({"model": {"provider": "openai_compatible",
        "model": "deepseek-flash", "base_url": "https://api.deepseek.com",
        "api_key_env": "SPROUT_FIXTURE_MODEL_KEY", "thinking_mode": "disabled",
        "max_output_tokens": 256}}).model
    model = _build_provider(settings.provider, settings.model, settings.base_url,
                             settings.api_key_env, settings)
    assert model.thinking_mode == "disabled" and model.max_output_tokens == 256
