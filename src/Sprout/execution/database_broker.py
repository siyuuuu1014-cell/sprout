"""Policy-controlled SQLite database access."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

from Sprout.execution.models import DatabaseResult
from Sprout.execution.policy_emit import emit_policy_decision
from Sprout.gateway.identity import Principal
from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.security.engine import PolicyEngine
from Sprout.task.models import DelegationScope
from Sprout.workspace.models import ResourceKind, ResourceRef, Workspace

if TYPE_CHECKING:
    from Sprout.events import EventBus


class DatabaseBroker:
    """Executes SQLite reads/writes only after a policy decision."""

    def __init__(self, policy: PolicyEngine, *, events: EventBus | None = None) -> None:
        self._policy = policy
        self._events = events

    async def query(
        self,
        workspace: Workspace,
        database: str | Path,
        sql: str,
        parameters: tuple[object, ...] = (),
        *,
        task_id: str = "",
        scope: DelegationScope | None = None,
        actor: Principal | None = None,
    ) -> DatabaseResult:
        from Sprout.task.paths import bound_scope_path, scope_path

        root = scope_path(str(workspace.root))
        target = scope_path(bound_scope_path(str(database), str(workspace.root)))
        if root is None or target is None or not root.contains(target):
            return DatabaseResult(database=str(database), allowed=False,
                                  reason="database_boundary_denied")
        database = Path(target.text())
        decision = await self._decide(
            workspace, database, ActionType.DB_READ, sql, task_id, scope, actor)
        if decision is not AccessDecision.ALLOW:
            return DatabaseResult(database=str(database), allowed=False, reason=decision.value)
        # Capture once through the existing secure-open primitive, then query an
        # isolated in-memory SQLite image. SQLite cannot reopen arbitrary paths.
        from Sprout.execution.file_access import file_operation
        from Sprout.execution.file_snapshot import _ReadOptions, checked_snapshot

        def authorize():
            request = ActionRequest(task_id=task_id, actor=actor or Principal(user_id="system"),
                action=ActionType.DB_READ,
                resource=ResourceRef(workspace.id, str(database), ResourceKind.DATA),
                arguments={"sql": sql}, scope=scope if scope is not None else DelegationScope())
            if self._policy.decide(request).decision is not AccessDecision.ALLOW:
                raise PermissionError("database_read_denied")

        try:
            snapshot = checked_snapshot(file_operation(
                workspace.root, database, "snapshot", authorize,
                content=_ReadOptions(16 * 1024 * 1024, None, Event(), authorize),
            ), 16 * 1024 * 1024, None)
            if not snapshot.ok:
                raise PermissionError("database_snapshot_unavailable")
            result = self._query(snapshot.data, sql, parameters, str(database))
            decision = await self._decide(
                workspace, database, ActionType.DB_READ, sql, task_id, scope, actor)
            if decision is not AccessDecision.ALLOW:
                raise PermissionError("database_read_denied")
            return result
        except (OSError, sqlite3.Error, ValueError) as exc:
            return DatabaseResult(database=str(database), allowed=False,
                                  reason=f"database_read_unavailable: {type(exc).__name__}: {exc}")

    async def execute(
        self,
        workspace: Workspace,
        database: str | Path,
        sql: str,
        parameters: tuple[object, ...] = (),
        *,
        sandbox: bool = False,
        task_id: str = "",
        scope: DelegationScope | None = None,
    ) -> DatabaseResult:
        decision = await self._decide(workspace, database, ActionType.DB_WRITE, sql, task_id, scope)
        if decision is AccessDecision.SANDBOX_ONLY and not sandbox:
            return DatabaseResult(
                database=str(database),
                allowed=False,
                reason="Database write is sandbox-only",
            )
        if decision is AccessDecision.SANDBOX_ONLY:
            return await asyncio.to_thread(self._execute, database, sql, parameters)
        return DatabaseResult(database=str(database), allowed=False, reason=decision.value)

    async def _decide(
        self,
        workspace: Workspace,
        database: str | Path,
        action: ActionType,
        sql: str,
        task_id: str,
        scope: DelegationScope | None = None,
        actor: Principal | None = None,
    ) -> AccessDecision:
        """Ask the policy engine, carrying the statement text with the request.

        The statement used to be left out of ``arguments``, so no layer ever saw
        it: ``PolicyRule.argument_guards`` could not target it and the hard floor
        (which scans ``command``/``cmd``/``argv``/``script``/``sql``) had nothing
        to read. A single ``ATTACH DATABASE '<abs path>'`` therefore reached any
        file on disk through an action the policy had rated ``ALLOW`` (audit R8).
        The database *path* is still the resource; the statement rides along as
        an argument, exactly as a command line rides along for ``process.run``.
        """
        resource = ResourceRef(
            workspace_id=workspace.id,
            path=str(database),
            kind=ResourceKind.DATA,
        )
        request = ActionRequest(
            task_id=task_id,
            actor=actor or Principal(user_id="system"),
            action=action,
            resource=resource,
            arguments={"sql": sql},
            scope=scope if scope is not None else DelegationScope(),
        )
        decision = self._policy.decide(request)
        await emit_policy_decision(self._events, request, decision)
        return decision.decision

    @staticmethod
    def _query(
        image: bytes,
        sql: str,
        parameters: tuple[object, ...],
        database: str,
    ) -> DatabaseResult:
        connection = sqlite3.connect(":memory:")
        try:
            connection.deserialize(image)
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
            connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536)
            connection.execute("PRAGMA query_only=ON")
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                       sqlite3.SQLITE_RECURSIVE}
            safe_pragmas = {"table_info", "table_xinfo", "index_info", "index_list",
                            "foreign_key_list", "database_list"}

            def authorize(action, first, second, db, source):
                if action == sqlite3.SQLITE_FUNCTION and str(second).lower() == "load_extension":
                    return sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_PRAGMA:
                    return (sqlite3.SQLITE_OK if str(first).lower() in safe_pragmas
                            else sqlite3.SQLITE_DENY)
                return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

            connection.set_authorizer(authorize)
            remaining = 1000

            def budget():
                nonlocal remaining
                remaining -= 1
                return int(remaining <= 0)

            connection.set_progress_handler(budget, 1000)
            cursor = connection.execute(sql, parameters)
            rows = cursor.fetchmany(1001)
            if len(rows) > 1000 or sum(len(repr(row)) for row in rows) > 1024 * 1024:
                raise sqlite3.DataError("database_result_budget_exceeded")
            return DatabaseResult(database=str(database), rows=tuple(rows))
        finally:
            connection.close()

    @staticmethod
    def _execute(
        database: str | Path,
        sql: str,
        parameters: tuple[object, ...],
    ) -> DatabaseResult:
        connection = sqlite3.connect(str(database))
        try:
            cursor = connection.execute(sql, parameters)
            connection.commit()
            return DatabaseResult(
                database=str(database),
                rowcount=cursor.rowcount,
            )
        finally:
            connection.close()
