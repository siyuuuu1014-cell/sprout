"""External Agent delegation gateway."""

from __future__ import annotations

from Sprout.gateway.identity import Principal
from Sprout.gateway.runtime_gateway import RuntimeGateway
from Sprout.gateway.task_adapter import GatewayRequest, GatewayResponse
from Sprout.runtime.runtime import Runtime
from Sprout.task.models import DelegationScope


class DelegationGateway:
    """Executes tasks delegated by external agents with an explicit scope."""

    def __init__(self, runtime: Runtime) -> None:
        self._gateway = RuntimeGateway(runtime, transport="delegation")

    async def execute(
        self,
        instruction: str,
        *,
        workspace_id: str,
        caller_id: str,
        scope: DelegationScope,
        execution_mode: str = "code",
        principal: Principal | None = None,
    ) -> GatewayResponse:
        # caller_id remains a display/compatibility argument, not authentication.
        if not isinstance(principal, Principal) or principal.authenticated is not True:
            raise PermissionError("An authenticated delegation host identity is required")
        if not isinstance(scope, DelegationScope):
            raise PermissionError("An explicit host scope is required")
        return await self._gateway.execute(
            GatewayRequest(
                transport="delegation",
                instruction=instruction,
                caller=principal,
                workspace_id=workspace_id,
                execution_mode=execution_mode,
                delegation_scope=scope,
            )
        )
