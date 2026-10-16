import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.llm.messages import LLMResponse, Usage
from Sprout.tests.live_model_budget import LiveModelBudget, LiveRequestLimit


async def test_concurrent_calls_never_exceed_authorized_limit():
    provider = SimpleNamespace(model="fixture", chat=AsyncMock(return_value=
        LLMResponse(content="ok", usage=Usage(prompt_tokens=3, completion_tokens=2))))
    budget = LiveModelBudget(provider, limit=2)
    results = await asyncio.gather(*(budget.chat([]) for _ in range(5)),
                                   return_exceptions=True)
    assert provider.chat.await_count == budget.attempted == 2
    assert sum(isinstance(result, LiveRequestLimit) for result in results) == 3
    assert budget.summary()["prompt_tokens"] == 6
    assert budget.summary()["completion_tokens"] == 4


async def test_failed_request_consumes_allowance_without_automatic_retry():
    provider = SimpleNamespace(model="fixture", chat=AsyncMock(side_effect=TimeoutError))
    budget = LiveModelBudget(provider, limit=1)
    with pytest.raises(TimeoutError):
        await budget.chat([])
    with pytest.raises(LiveRequestLimit):
        await budget.chat([])
    assert provider.chat.await_count == 1
    assert budget.summary()["requests_attempted"] == 1


@pytest.mark.parametrize("limit", [True, 0, -1, 21, "20"])
def test_invalid_authorization_limit_rejected(limit):
    with pytest.raises(ValueError):
        LiveModelBudget(SimpleNamespace(model="fixture"), limit=limit)


async def test_twenty_request_authorization_is_an_exact_cap():
    provider = SimpleNamespace(model="fixture", chat=AsyncMock(return_value=LLMResponse()))
    budget = LiveModelBudget(provider, limit=20)
    for _ in range(20):
        await budget.chat([])
    with pytest.raises(LiveRequestLimit):
        await budget.chat([])
    assert provider.chat.await_count == budget.attempted == 20
