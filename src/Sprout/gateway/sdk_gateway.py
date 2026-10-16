"""Python SDK gateway for embedding SEMA in another application."""

from __future__ import annotations

from Sprout.gateway.identity import Principal
from Sprout.gateway.runtime_gateway import RuntimeGateway
from Sprout.gateway.task_adapter import GatewayRequest, GatewayResponse
from Sprout.runtime.runtime import Runtime
from Sprout.task.models import DelegationScope


class SDKGateway:
    """Runtime SDK facade used by Python applications."""

    def __init__(self, runtime: Runtime, *, principal: Principal | None = None) -> None:
        # Embedding-host configuration, not an identity supplied in a request body.
        self._principal = principal
        self._gateway = RuntimeGateway(runtime, transport="sdk")

    async def task_report(self, task_id: str):
        return await self._gateway.queries.report(task_id, principal=self._query_principal())

    async def sync_task(self, task_id: str, *, submission_id=None, wait_seconds=0):
        return await self._gateway.actions.sync(
            task_id, principal=self._query_principal(),
            submission_id=submission_id, wait_seconds=wait_seconds,
        )

    async def submit_persisted_task(self, task_id: str):
        return await self._gateway.actions.submit(task_id, principal=self._query_principal())

    async def retry_submission(self, task_id: str, *, submission_id: str):
        return await self._gateway.actions.retry_submission(
            task_id, principal=self._query_principal(), submission_id=submission_id)

    async def observe_task(self, task_id: str, *, submission_id=None, wait_seconds=0):
        return await self._gateway.queries.observe(
            task_id, principal=self._query_principal(),
            submission_id=submission_id, wait_seconds=wait_seconds,
        )

    def _query_principal(self) -> Principal:
        if self._principal is None:
            raise PermissionError("Configure a trusted embedding-host principal for task queries")
        return self._principal

    async def execute(
        self,
        instruction: str,
        *,
        workspace_id: str,
        caller_id: str = "sdk-user",
        delegation_scope: DelegationScope | None = None,
        execution_mode: str = "code",
    ) -> GatewayResponse:
        return await self._gateway.execute(
            GatewayRequest(
                transport="sdk",
                instruction=instruction,
                caller=self._principal or Principal(user_id=caller_id),
                workspace_id=workspace_id,
                execution_mode=execution_mode,
                delegation_scope=delegation_scope or DelegationScope(),
            )
        )
