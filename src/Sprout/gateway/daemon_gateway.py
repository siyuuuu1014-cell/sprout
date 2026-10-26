"""Daemon gateway for external HTTP/RPC clients."""

from __future__ import annotations

from Sprout.gateway.identity import Principal
from Sprout.gateway.project_gateway import ProjectGateway
from Sprout.gateway.task_queries import TaskQueries
from Sprout.runtime.runtime import Runtime
from Sprout.task.models import DelegationScope


class DaemonGateway:
    """External-facing service gateway built on the project gateway facade."""

    def __init__(self, runtime: Runtime) -> None:
        self._queries = TaskQueries(runtime)
        self._project = ProjectGateway(
            runtime,
            transport="daemon",
            default_user="daemon-client",
        )

    async def open_workspace(self, path: str):
        return await self._project.open_workspace(path)

    async def list_workspaces(self):
        return await self._project.list_workspaces()

    async def create_task(
        self, workspace_id: str, instruction: str, *, execution_mode: str = "code",
        principal: Principal | None = None, delegation_scope: DelegationScope | None = None,
    ):
        # These objects are supplied by the embedding host, never deserialized
        # from an HTTP/RPC request body.
        if not isinstance(principal, Principal) or principal.authenticated is not True:
            raise PermissionError("An authenticated daemon host identity is required")
        if not isinstance(delegation_scope, DelegationScope):
            raise PermissionError("An explicit host scope is required")
        return await self._project.create_task(
            workspace_id, instruction, execution_mode=execution_mode,
            principal=principal, delegation_scope=delegation_scope,
        )

    async def run_task(self, task_id: str, *, principal: Principal | None = None):
        await self._queries._owned_task(task_id, principal)
        return await self._project.run_task(task_id)

    async def list_changes(self, task_id: str):
        return await self._project.list_changes(task_id)
