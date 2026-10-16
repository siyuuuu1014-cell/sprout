"""Task-local context, persistent metadata authority; no global mutable budget."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict

from Sprout.llm.base import ModelBudgetExhausted

_scope = ContextVar("task_model_budget", default=None)


@contextmanager
def task_model_budget(metadata, task_id, node_id, limit):
    token = _scope.set((metadata, task_id, node_id, limit) if limit > 0 else None)
    try:
        yield
    finally:
        _scope.reset(token)


async def reserve_attempt():
    scope = _scope.get()
    if scope is None:
        return None
    metadata, task_id, node_id, limit = scope
    try:
        request = await metadata.reserve_model_request(task_id, node_id, limit)
    except Exception as exc:
        raise ModelBudgetExhausted("Persistent model budget unavailable; no request sent") from exc
    if request is None:
        raise ModelBudgetExhausted("Persistent task model request limit exhausted or changed")
    return metadata, request


async def settle_attempt(ticket, response=None):
    if ticket is None:
        return
    metadata, request = ticket
    try:
        settled = await asyncio.shield(metadata.finish_model_request(request,
            "responded" if response is not None else "unconfirmed",
            asdict(response.usage) if response is not None else {}))
        if not settled:
            raise ValueError("Model request settlement unavailable")
    except Exception as exc:
        raise ModelBudgetExhausted("Model budget settlement requires reconciliation") from exc
