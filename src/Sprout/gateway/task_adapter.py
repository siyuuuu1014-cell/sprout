"""Interface-layer Task adapter.

Every transport eventually becomes a Task. This module provides the common
conversion path from gateway input into the task domain model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from Sprout.gateway.identity import Principal
from Sprout.message.models import Message
from Sprout.task.models import (
    DelegationScope,
    Task,
    TaskBudget,
    TaskExecutionMode,
    execution_metadata,
)


@dataclass(frozen=True, slots=True)
class GatewayRequest:
    transport: str
    instruction: str
    caller: Principal = field(default_factory=lambda: Principal(user_id="system"))
    workspace_id: str = ""
    delegation_scope: DelegationScope = field(default_factory=DelegationScope)
    budget: TaskBudget = field(default_factory=TaskBudget)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    execution_mode: str = TaskExecutionMode.CODE


@dataclass(frozen=True, slots=True)
class GatewayResponse:
    task_id: str
    status: str
    content: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


class TaskAdapter:
    """Converts gateway requests and unified messages into Tasks."""

    def to_task(self, request: GatewayRequest) -> Task:
        return Task(
            workspace_id=request.workspace_id,
            instruction=request.instruction,
            actor=request.caller,
            # Transport labels identify a channel, not the authenticated host.
            # Preserve the complete supplied Principal; never promote its trust.
            source=request.caller.source,
            delegation_scope=request.delegation_scope,
            budget=request.budget,
            metadata=execution_metadata(request.metadata, request.execution_mode),
        )

    def from_message(
        self,
        message: Message,
        *,
        workspace_id: str = "",
        delegation_scope: DelegationScope | None = None,
    ) -> GatewayRequest:
        from Sprout.gateway.identity import principal_from_message

        return GatewayRequest(
            transport=message.channel,
            instruction=message.content,
            caller=principal_from_message(message),
            workspace_id=workspace_id,
            delegation_scope=delegation_scope or DelegationScope(),
            metadata=dict(message.metadata),
            execution_mode=message.metadata.get("execution_mode", TaskExecutionMode.CODE),
        )
