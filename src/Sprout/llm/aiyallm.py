"""``aiyallm`` model adapter."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from aiyallm import Aiyallm
from aiyallm import OpenAICompatibleProvider as AiyallmOpenAICompatibleProvider

from Sprout.llm.diagnostics import record_completion
from Sprout.llm.messages import (
    LLMMessage,
    LLMResponse,
    LLMStreamEvent,
    Role,
    ToolCall,
    Usage,
)

if TYPE_CHECKING:
    from Sprout.tools.spec import ToolSpec


class _ToolCompatibleOpenAIProvider(AiyallmOpenAICompatibleProvider):
    """aiyallm 0.1.x provider extension; keep its routing and SDK transport.

    The public aiyallm request has no extra_body option (metadata is NOT an
    API-body extension). This common builder serves sync and async streams
    as well as normal calls. No installed dependency is patched.
    """

    def __init__(self, *args, thinking_mode=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._thinking_mode = thinking_mode

    async def _aiter_stream(self, stream, request):
        async for chunk in super()._aiter_stream(stream, request):
            if chunk.raw is None and chunk.usage is not None:
                # aiyallm 0.1.4 consumes this sentinel for its own ledger and
                # never forwards it. Send the real usage once before preserving
                # the original sentinel for that ledger; do not invent tokens.
                yield replace(chunk, raw={"usage_forwarded": True})
            yield chunk

    def _build_chat_kwargs(self, request, *, stream):
        kwargs = super()._build_chat_kwargs(request, stream=stream)
        mode = self._thinking_mode
        if mode is None and request.model.lower().startswith("deepseek-"):
            # Sprout does not persist/round-trip vendor reasoning. DeepSeek
            # tool conversations must therefore explicitly use non-thinking.
            mode = "disabled"
        if mode is not None:
            kwargs["extra_body"] = {"thinking": {"type": mode}}
        return kwargs


def _configured_provider(spec, thinking_mode):
    """Preserve aiyallm's OpenAI provider-spec defaults, names and accounts."""
    kind = str(spec.get("type") or spec.get("kind") or "openai").lower()
    if kind not in {"openai", "openai_compatible", "openai-compatible"}:
        return dict(spec)
    keys = (
        "account", "api_key", "api_key_env", "api_key_prompt", "base_url", "models", "timeout",
        "api_key_required", "organization", "project", "default_headers", "stream_include_usage",
    )
    kwargs = {key: spec[key] for key in keys if key in spec}
    return _ToolCompatibleOpenAIProvider(
        name=str(spec.get("name") or kind),
        allow_unknown_models=bool(spec.get("allow_unknown_models", True)),
        thinking_mode=thinking_mode, **kwargs,
    )


class AiyallmProvider:
    """Adapt the ``aiyallm`` package to the Sprout :class:`ModelProvider` protocol."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None = None,
        api_key_env: str | None = None,
        name: str = "aiyallm",
        timeout_seconds: float = 60.0,
        temperature: float | None = None,
        thinking_mode: str | None = None,
        max_output_tokens: int | None = None,
        providers: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        if thinking_mode not in {None, "disabled"}:
            raise ValueError(
                "Only disabled thinking mode is supported; reasoning round-trip is absent")
        if max_output_tokens is not None and (
            type(max_output_tokens) is not int or max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        self.thinking_mode = thinking_mode
        self.max_output_tokens = max_output_tokens
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.api_key_env = api_key_env
        self.last_usage: Usage | None = None

        resolved_key = api_key
        if not resolved_key and api_key_env:
            resolved_key = os.environ.get(api_key_env)
        if providers:
            # Preserve provider names and model lists for refs such as
            # ``deepseek/deepseek-flash``.
            self._client = Aiyallm(
                providers=[_configured_provider(item, thinking_mode) for item in providers],
                default_model=self.model or None,
            )
        else:
            provider = _ToolCompatibleOpenAIProvider(
                "aiyallm-openai-compatible",
                api_key=resolved_key,
                api_key_required=False,
                base_url=self.base_url,
                models=[self.model],
                timeout=self.timeout_seconds,
                allow_unknown_models=True,
                thinking_mode=thinking_mode,
            )
            self._client = Aiyallm(providers=[provider], default_model=self.model)

    async def chat(
        self, messages: Sequence[LLMMessage], *, tools: Sequence[ToolSpec] = ()
    ) -> LLMResponse:
        """Call the configured model through the ``aiyallm`` package."""
        encoded_messages = [self._encode_message(message) for message in messages]
        encoded_tools = [self._encode_tool(spec) for spec in tools] if tools else None

        response = await self._client.achat(
            messages=encoded_messages,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_output_tokens,
            tools=encoded_tools,
        )

        tool_calls = tuple(
            ToolCall(
                id=call.id,
                name=call.name,
                arguments=call.arguments_dict,
            )
            for call in response.tool_calls
        )
        self.last_usage = self._decode_usage(response.usage)
        record_completion(self, response.finish_reason,
                          self.last_usage if response.usage is not None else None)
        return LLMResponse(
            content=response.content or "",
            tool_calls=tool_calls,
            finish_reason=response.finish_reason,
            model=response.model or self.model,
            usage=self._decode_usage(response.usage),
        )

    async def stream(
        self, messages: Sequence[LLMMessage], *, tools: Sequence[ToolSpec] = ()
    ):
        async for event in self.stream_events(messages, tools=tools):
            yield event.content

    async def stream_events(
        self, messages: Sequence[LLMMessage], *, tools: Sequence[ToolSpec] = ()
    ):
        """Stream structured events from ``aiyallm`` chat chunks."""
        encoded_messages = [self._encode_message(message) for message in messages]
        encoded_tools = [self._encode_tool(spec) for spec in tools] if tools else None
        self.last_usage = None
        finish_reason = None
        async for chunk in self._client.astream(
            messages=encoded_messages, model=self.model, temperature=self.temperature,
            max_tokens=self.max_output_tokens, tools=encoded_tools,
        ):
            usage = self._decode_usage(chunk.usage) if chunk.usage is not None else None
            if usage is not None:
                self.last_usage = usage
            finish_reason = chunk.finish_reason or finish_reason
            yield LLMStreamEvent(
                content=chunk.content or "",
                tool_calls=tuple(ToolCall(id=call.id, name=call.name, arguments=call.arguments_dict)
                                 for call in chunk.tool_calls or ()),
                finish_reason=chunk.finish_reason, usage=usage,
                model=getattr(chunk, "model", None) or self.model,
            )
        record_completion(self, finish_reason, self.last_usage)


    @staticmethod
    def _encode_tool(spec: ToolSpec) -> dict[str, Any]:
        parameters = dict(spec.input_schema) if spec.input_schema else {
            "type": "object",
            "properties": {},
        }
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": parameters,
            },
        }

    @staticmethod
    def _encode_message(message: LLMMessage) -> dict[str, Any]:
        encoded: dict[str, Any] = {"role": message.role.value}
        if message.content is not None:
            encoded["content"] = message.content
        if message.tool_calls:
            encoded["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            dict(call.arguments), ensure_ascii=False, default=str
                        ),
                    },
                }
                for call in message.tool_calls
            ]
        if message.role is Role.TOOL:
            encoded["tool_call_id"] = message.tool_call_id
            if message.name:
                encoded["name"] = message.name
        return encoded

    @staticmethod
    def _decode_usage(usage: Any) -> Usage:
        return Usage(
            prompt_tokens=int(getattr(usage, "input_tokens", 0)),
            completion_tokens=int(getattr(usage, "output_tokens", 0)),
            total_tokens=int(
                getattr(
                    usage,
                    "total_tokens",
                    getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0),
                )
            ),
        )
