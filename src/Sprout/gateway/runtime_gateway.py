"""Runtime-backed gateway facade.

Transport adapters can call this facade instead of touching Runtime internals
directly. It converts gateway input into Tasks and returns a GatewayResponse.
"""

from __future__ import annotations

from Sprout.events import AUTH_ROLES_DISCARDED, Event
from Sprout.gateway.identity import Principal, discarded_roles
from Sprout.gateway.task_actions import TaskActions
from Sprout.gateway.task_adapter import (
    GatewayRequest,
    GatewayResponse,
    TaskAdapter,
)
from Sprout.gateway.task_queries import TaskQueries
from Sprout.message.models import Message
from Sprout.runtime.runtime import Runtime


class RuntimeGateway:
    """Small interface-layer facade over one Runtime instance."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        transport: str = "gateway",
        adapter: TaskAdapter | None = None,
    ) -> None:
        self._runtime = runtime
        self._transport = transport
        self._adapter = adapter or TaskAdapter()
        self.queries = TaskQueries(runtime)
        self.actions = TaskActions(runtime)

    async def execute(self, request: GatewayRequest) -> GatewayResponse:
        task = self._adapter.to_task(request)
        if (request.workspace_id and request.caller.authenticated is True
                and request.caller.source and request.caller.user_id
                and request.delegation_scope.allowed_paths
                and not request.delegation_scope.is_expired()):
            # A transport label/user name is not an authenticated entry. Bind
            # the supplied host identity to a real Session and persisted Task;
            # request metadata cannot select another Session's authority.
            session = await self._runtime._sessions.resolve(
                Message(content=request.instruction, channel=request.caller.source),
                principal=request.caller,
            )
            task = await self._runtime.create_task(
                request.workspace_id, request.instruction, actor=request.caller,
                source=request.caller.source, delegation_scope=request.delegation_scope,
                budget=request.budget, execution_mode=request.execution_mode,
                metadata={**dict(task.metadata), "session_id": session.id},
            )
        # Missing authority remains an unprivileged request. The existing
        # Runtime/Broker refusal (including its public result/error shape) is
        # authoritative; do not authenticate it or manufacture a Session.
        result = await self._runtime.execute(task)
        return GatewayResponse(
            task_id=result.task_id,
            status=result.status.value,
            content=result.content,
            metadata=dict(result.metrics),
        )

    async def execute_message(self, message: Message) -> GatewayResponse:
        # Identity never comes from the message body (AUTHZ §2.1): roles found in
        # ``message.metadata`` are discarded and the attempt is audited.
        roles = discarded_roles(message)
        if roles:
            await self._runtime.events.publish(
                Event(
                    AUTH_ROLES_DISCARDED,
                    {
                        "user_id": message.user_id,
                        "channel": message.channel,
                        "roles": list(roles),
                    },
                    message.id,
                )
            )
        request = self._adapter.from_message(
            message,
            workspace_id=message.metadata.get("workspace_id", ""),
        )
        return await self.execute(request)

    async def handle_message(
        self, message: Message, *, principal: Principal | None = None, foreground: bool = False
    ) -> GatewayResponse:
        if foreground:
            outbound = await self._runtime.handle(message, principal=principal, foreground=True)
        else:
            outbound = await self._runtime.handle(message, principal=principal)
        return GatewayResponse(
            task_id=outbound.session_id or "",
            status="completed",
            content=outbound.content,
            metadata={
                "session_id": outbound.session_id,
                "correlation_id": outbound.correlation_id,
                **outbound.metadata,
            },
        )

    async def handle_stream(self, message: Message, *, principal: Principal | None = None):
        async for chunk in self._runtime.handle_stream(message, principal=principal):
            yield chunk
