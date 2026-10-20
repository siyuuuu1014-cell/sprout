"""Concrete transport gateways for CLI, Web, and MCP."""

from __future__ import annotations

from Sprout.gateway.identity import Principal
from Sprout.gateway.runtime_gateway import RuntimeGateway
from Sprout.gateway.task_adapter import GatewayRequest, GatewayResponse
from Sprout.message.models import Message
from Sprout.runtime.runtime import Runtime


class CLIGateway:
    def __init__(self, runtime: Runtime) -> None:
        self._gateway = RuntimeGateway(runtime, transport="cli")

    async def execute(
        self,
        instruction: str,
        *,
        workspace_id: str = "",
        user_id: str = "cli-user",
        execution_mode: str = "code",
    ) -> GatewayResponse:
        from Sprout.gateway.temporal_bridge import submit_gateway_workflow

        await submit_gateway_workflow(
            {
                "transport": "cli",
                "execution_mode": execution_mode,
                "id": workspace_id or "cli",
                "instruction": instruction,
                "user_id": user_id,
            }
        )
        return await self._gateway.execute(
            GatewayRequest(
                transport="cli",
                execution_mode=execution_mode,
                instruction=instruction,
                caller=Principal(user_id=user_id),
                workspace_id=workspace_id,
            )
        )

    async def handle_message(
        self, message: Message, *, foreground: bool = False
    ) -> GatewayResponse:
        from Sprout.gateway.local_identity import current_cli_principal

        return await self._gateway.handle_message(
            message, principal=current_cli_principal(), foreground=foreground
        )

    async def handle_stream(self, message: Message):
        from Sprout.gateway.local_identity import current_cli_principal

        async for chunk in self._gateway.handle_stream(message, principal=current_cli_principal()):
            yield chunk


class WebGateway:
    def __init__(self, runtime: Runtime) -> None:
        self._gateway = RuntimeGateway(runtime, transport="web")

    async def execute(
        self,
        instruction: str,
        *,
        workspace_id: str = "",
        user_id: str = "web-user",
        execution_mode: str = "code",
    ) -> GatewayResponse:
        from Sprout.gateway.temporal_bridge import submit_gateway_workflow

        await submit_gateway_workflow(
            {
                "transport": "web",
                "execution_mode": execution_mode,
                "id": workspace_id or "web",
                "instruction": instruction,
                "user_id": user_id,
            }
        )
        return await self._gateway.execute(
            GatewayRequest(
                transport="web",
                execution_mode=execution_mode,
                instruction=instruction,
                caller=Principal(user_id=user_id),
                workspace_id=workspace_id,
            )
        )

    async def handle_message(
        self, message: Message, *, principal: Principal | None = None
    ) -> GatewayResponse:
        return await self._gateway.handle_message(message, principal=principal)

    async def handle_stream(self, message: Message, *, principal: Principal | None = None):
        async for chunk in self._gateway.handle_stream(message, principal=principal):
            yield chunk


class MCPGateway:
    def __init__(self, runtime: Runtime) -> None:
        self._gateway = RuntimeGateway(runtime, transport="mcp")

    async def execute(
        self,
        instruction: str,
        *,
        workspace_id: str = "",
        user_id: str = "mcp-client",
        execution_mode: str = "code",
    ) -> GatewayResponse:
        from Sprout.gateway.temporal_bridge import submit_gateway_workflow

        await submit_gateway_workflow(
            {
                "transport": "mcp",
                "execution_mode": execution_mode,
                "id": workspace_id or "mcp",
                "instruction": instruction,
                "user_id": user_id,
            }
        )
        return await self._gateway.execute(
            GatewayRequest(
                transport="mcp",
                execution_mode=execution_mode,
                instruction=instruction,
                caller=Principal(user_id=user_id),
                workspace_id=workspace_id,
            )
        )

    async def handle_message(self, message: Message) -> GatewayResponse:
        return await self._gateway.handle_message(message)
