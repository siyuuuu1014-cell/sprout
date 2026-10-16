"""Explicit live-experiment call cap; wrap the direct provider, not its retries.

This is experiment support, not production task-wide token or money budgeting.
Count before awaiting HTTP so failed/unknown requests also consume allowance.
"""

import asyncio

from Sprout.llm.base import ModelBudgetExhausted


class LiveRequestLimit(ModelBudgetExhausted):
    """No further paid calls are authorized for this experiment."""


class LiveModelBudget:
    def __init__(self, provider, *, limit=10):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Live experiment request limit must be 1..20")
        self.provider = provider
        self.name = "bounded-live-experiment"
        self.model = provider.model
        self.limit = limit
        self.attempted = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self._lock = asyncio.Lock()

    async def chat(self, messages, *, tools=()):
        async with self._lock:
            if self.attempted >= self.limit:
                raise LiveRequestLimit("Authorized live request count exhausted")
            self.attempted += 1
        response = await self.provider.chat(messages, tools=tools)
        self.prompt_tokens += response.usage.prompt_tokens
        self.completion_tokens += response.usage.completion_tokens
        return response

    def summary(self):
        return {"requests_attempted": self.attempted, "request_limit": self.limit,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "usage_scope": "successful_responses_only",
                "money_hard_cap": False}
