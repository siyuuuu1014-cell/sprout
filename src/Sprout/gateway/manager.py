"""Interface-layer gateway manager."""

from __future__ import annotations

from Sprout.gateway.identity import Principal
from Sprout.gateway.task_adapter import GatewayResponse
from Sprout.gateway.transport_gateways import CLIGateway, MCPGateway, WebGateway
from Sprout.runtime.runtime import Runtime
from Sprout.task.models import DelegationScope


class GatewayManager:
    """Owns the concrete transport gateways for a Runtime."""

    def __init__(self, runtime: Runtime) -> None:
        self._gateways = {
            "cli": CLIGateway(runtime),
            "web": WebGateway(runtime),
            "mcp": MCPGateway(runtime),
        }

    def get(self, transport: str):
        try:
            return self._gateways[transport]
        except KeyError as exc:
            raise LookupError(f"Unknown transport gateway: {transport}") from exc

    async def execute(
        self,
        transport: str,
        instruction: str,
        *,
        workspace_id: str = "",
        user_id: str | None = None,
        execution_mode: str = "code",
        principal: Principal | None = None,
        delegation_scope: DelegationScope | None = None,
    ) -> GatewayResponse:
        gateway = self.get(transport)
        default_user = {
            "cli": "cli-user",
            "web": "web-user",
            "mcp": "mcp-client",
        }.get(transport, "gateway-user")
        return await gateway.execute(
            instruction,
            workspace_id=workspace_id,
            user_id=user_id or default_user,
            execution_mode=execution_mode,
            principal=principal,
            delegation_scope=delegation_scope,
        )
