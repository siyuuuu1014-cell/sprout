from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.llm.messages import LLMResponse, ToolCall
from Sprout.tests import deepseek_connectivity_probe as module


def setup(monkeypatch, *, failed=False):
    settings = SimpleNamespace(provider="openai_compatible", model="deepseek-flash",
        base_url="https://api.deepseek.com", api_key_env="DEEPSEEK_API_KEY",
        thinking_mode="disabled", max_output_tokens=256)
    monkeypatch.setattr(module, "load_settings", lambda path: SimpleNamespace(model=settings))
    monkeypatch.setattr(module, "load_env_file", Mock())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-never-sent")
    responses = [LLMResponse(content="OK"), LLMResponse(tool_calls=(
        ToolCall("fixture", "add", {"a": 2, "b": 3}),))]
    if failed:
        responses = [RuntimeError("Model request failed with HTTP 401: fixture-never-sent")]
    model = SimpleNamespace(chat=AsyncMock(side_effect=responses))
    factory = Mock(return_value=model)
    monkeypatch.setattr(module, "_build_provider", factory)
    return settings, model, factory


async def test_probe_makes_exactly_two_direct_calls_and_does_not_export_secret(monkeypatch):
    _, model, _ = setup(monkeypatch)
    result = await module.probe("fixture.toml")
    assert result["passed"] and result["requests_attempted"] == 2
    assert model.chat.await_count == 2 and result["automatic_retry"] is False
    assert "fixture-never-sent" not in str(result)
    assert result["coding_task_started"] is False


async def test_error_stops_without_retry_or_error_body_export(monkeypatch):
    _, model, _ = setup(monkeypatch, failed=True)
    result = await module.probe("fixture.toml")
    assert not result["passed"] and result["requests_attempted"] == 1
    assert result["http_status"] == 401
    model.chat.assert_awaited_once()
    assert "fixture-never-sent" not in str(result)


async def test_no_dedicated_key_does_not_fall_back_to_other_provider(monkeypatch):
    _, _, factory = setup(monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "different-provider-fixture")
    with pytest.raises(ValueError, match="no credential fallback"):
        await module.probe("fixture.toml")
    factory.assert_not_called()


async def test_output_limit_above_authorized_scope_is_refused(monkeypatch):
    settings, _, factory = setup(monkeypatch)
    settings.max_output_tokens = 257
    with pytest.raises(ValueError, match="output limit"):
        await module.probe("fixture.toml")
    factory.assert_not_called()


async def test_secret_in_variable_name_field_is_refused_without_echo(monkeypatch):
    settings, _, factory = setup(monkeypatch)
    settings.api_key_env = "fixture-sensitive-value"
    with pytest.raises(module.ProbeConfigurationError) as caught:
        await module.probe("fixture.toml")
    assert caught.value.code == "api_key_env_is_not_variable_name"
    assert "fixture-sensitive-value" not in str(caught.value)
    factory.assert_not_called()
