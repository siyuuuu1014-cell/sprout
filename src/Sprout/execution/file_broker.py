"""Policy-controlled sandbox file writes (AUTHZ §3.2).

Writes used to be ``SANDBOX_ONLY`` regardless of what the file *was*, which
meant a sandbox could overwrite ``.env`` or a private key. The target is now
classified against the sandbox root (``workspace/classifier.py``) and the kind
drives the decision: SECRET/EXTERNAL are denied outright, SENSITIVE needs a
human, everything else stays sandbox-first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from Sprout.execution.file_access import FileBoundaryError, file_operation
from Sprout.execution.models import FileResult, SandboxRef
from Sprout.execution.policy_emit import emit_policy_decision
from Sprout.security.access import AccessDecision, ActionRequest, ActionType, PolicyDecision
from Sprout.security.engine import PolicyEngine
from Sprout.task.models import DelegationScope
from Sprout.task.paths import bound_scope_path, scope_path
from Sprout.workspace.classifier import classify, is_device_namespace
from Sprout.workspace.models import ResourceKind, ResourceRef

if TYPE_CHECKING:
    from Sprout.events import EventBus
    from Sprout.security.approval import ApprovalManager


class FileBroker:
    """Writes files only inside an explicit sandbox root."""

    def __init__(
        self,
        policy: PolicyEngine,
        *,
        classify_overrides: Mapping[str, str] | None = None,
        events: EventBus | None = None,
        approvals: ApprovalManager | None = None,
    ) -> None:
        self._policy = policy
        self._classify_overrides = dict(classify_overrides or {})
        self._events = events
        self._approvals = approvals

    def _classify(self, target: Path, sandbox: SandboxRef) -> ResourceKind:
        return classify(
            target.relative_to(self._root(sandbox)).as_posix(),
            overrides=self._classify_overrides,
        )

    @staticmethod
    def _root(sandbox: SandboxRef) -> Path:
        parsed = scope_path(str(sandbox.root))
        if parsed is None or not parsed.anchor:
            raise FileBoundaryError("explicit_absolute_root_required")
        return Path(parsed.text())

    async def _access(
        self, sandbox, target, request, operation, *, approved=False, preflight=False,
        content="", newline=None,
    ):
        """Reauthorize the pinned resource just before data access/effects.

        The async policy event is preserved; a final decision emits the same
        event contract after the worker finishes. No caller can supply approved
        through the public API: delete obtains it from authorize_delete only.
        """
        final = []

        def authorize():
            decision = self._policy.decide(request)
            if not request.scope.permits(request.action.value, request.resource.path):
                expired = request.scope.is_expired()
                decision = PolicyDecision(
                    AccessDecision.DENY, "DelegationScope expired or outside resource boundary",
                    matched_rules=("scope:expired" if expired else "scope:denied",),
                )
            final.append(decision)
            allowed = ({AccessDecision.SANDBOX_ONLY} if operation == "write" else
                       {AccessDecision.ALLOW, AccessDecision.ALLOW_REDACTED}
                       if request.action is ActionType.FILE_READ else {AccessDecision.ALLOW})
            if (request.action is ActionType.FILE_DELETE and (approved or preflight)):
                allowed.add(AccessDecision.REQUIRE_APPROVAL)
            if decision.decision not in allowed:
                raise FileBoundaryError("authorization_changed_or_denied")

        try:
            value = await asyncio.to_thread(
                file_operation, self._root(sandbox), target, operation, authorize,
                content=content, newline=newline,
            )
            return True, value
        except (OSError, ValueError) as exc:
            # Never return guessed content or retry with ordinary pathname I/O.
            code = str(exc) if isinstance(exc, FileBoundaryError) else type(exc).__name__
            return False, f"Secure file access refused ({code})"
        finally:
            if final:
                await emit_policy_decision(self._events, request, final[-1])

    async def write_text(
        self,
        sandbox: SandboxRef,
        relative_path: str | Path,
        content: str,
        *,
        task_id: str = "",
        scope: DelegationScope | None = None,
        newline: str | None = None,
    ) -> FileResult:
        target = self._safe_target(sandbox, relative_path)
        if target is None:
            return FileResult(path=str(relative_path), wrote=False, reason="Path escapes sandbox")

        kind = self._classify(target, sandbox)
        request = ActionRequest(
            task_id=task_id,
            action=ActionType.FILE_WRITE,
            resource=ResourceRef(
                workspace_id=sandbox.id,
                path=target.as_posix(),
                kind=kind,
            ),
            scope=(scope if scope is not None else DelegationScope()).bind_paths(
                str(self._root(sandbox)),
            ),
        )
        decision = self._policy.decide(request)
        await emit_policy_decision(self._events, request, decision)
        if decision.decision is not AccessDecision.SANDBOX_ONLY:
            return FileResult(
                path=str(relative_path),
                wrote=False,
                reason=decision.reason,
                resource_kind=kind.value,
            )

        ok, value = await self._access(sandbox, target, request, "write",
                                       content=content, newline=newline)
        return FileResult(path=str(relative_path), wrote=ok, resource_kind=kind.value,
                          reason="" if ok else value)

    async def read_text(
        self,
        sandbox: SandboxRef,
        relative_path: str | Path,
        *,
        task_id: str = "",
        scope: DelegationScope | None = None,
    ) -> FileResult:
        target = self._safe_target(sandbox, relative_path)
        if target is None:
            return FileResult(path=str(relative_path), wrote=False, reason="Path escapes sandbox")

        kind = self._classify(target, sandbox)
        request = ActionRequest(
            task_id=task_id,
            action=ActionType.FILE_READ,
            resource=ResourceRef(
                workspace_id=sandbox.id,
                path=target.as_posix(),
                kind=kind,
            ),
            scope=(scope if scope is not None else DelegationScope()).bind_paths(
                str(self._root(sandbox)),
            ),
        )
        decision = self._policy.decide(request)
        await emit_policy_decision(self._events, request, decision)
        if decision.decision not in {AccessDecision.ALLOW, AccessDecision.ALLOW_REDACTED}:
            return FileResult(
                path=str(relative_path),
                wrote=False,
                reason=decision.reason,
                resource_kind=kind.value,
            )

        ok, content = await self._access(sandbox, target, request, "read")
        return FileResult(
            path=str(relative_path),
            wrote=ok,
            resource_kind=kind.value,
            content=content if ok else "",
            reason="" if ok else content,
        )

    async def exists(
        self,
        sandbox: SandboxRef,
        relative_path: str | Path,
        *,
        task_id: str = "",
        scope: DelegationScope | None = None,
    ) -> bool:
        """Return whether ``relative_path`` exists inside the sandbox root."""
        target = self._safe_target(sandbox, relative_path)
        if target is None:
            return False
        kind = self._classify(target, sandbox)
        request = ActionRequest(
            task_id=task_id, action=ActionType.FILE_READ,
            resource=ResourceRef(workspace_id=sandbox.id, path=target.as_posix(), kind=kind),
            scope=(scope if scope is not None else DelegationScope()).bind_paths(
                str(self._root(sandbox)),
            ),
        )
        decision = self._policy.decide(request)
        await emit_policy_decision(self._events, request, decision)
        if decision.decision not in {AccessDecision.ALLOW, AccessDecision.ALLOW_REDACTED}:
            return False
        ok, value = await self._access(sandbox, target, request, "exists")
        return ok and bool(value)

    async def delete(
        self,
        sandbox: SandboxRef,
        relative_path: str | Path,
        *,
        task_id: str = "",
        scope: DelegationScope | None = None,
        source: str = "interactive",
        requested_by: str = "system",
    ) -> FileResult:
        result = await self.authorize_delete(
            sandbox,
            relative_path,
            task_id=task_id,
            scope=scope,
            source=source,
            requested_by=requested_by,
        )
        if not result.wrote:
            return result
        target = self._safe_target(sandbox, relative_path)
        if target is None:
            return FileResult(path=str(relative_path), wrote=False, reason="Path escapes sandbox")
        request = ActionRequest(
            task_id=task_id, action=ActionType.FILE_DELETE,
            resource=ResourceRef(workspace_id=sandbox.id, path=target.as_posix(),
                                 kind=self._classify(target, sandbox)),
            scope=(scope if scope is not None else DelegationScope()).bind_paths(
                str(self._root(sandbox)),
            ),
        )
        approved = False
        if self._approvals is not None and self._approvals.asks_a_human(source):
            approved = await self._approvals.is_approved(
                "file_delete", {"path": target.as_posix(), "sandbox_id": sandbox.id},
                task_id=task_id, source=source, requested_by=requested_by,
                resource_scope=sandbox.id,
            )
        ok, value = await self._access(sandbox, target, request, "delete", approved=approved)
        return result if ok else FileResult(path=str(relative_path), wrote=False, reason=value)

    async def authorize_delete(
        self,
        sandbox: SandboxRef,
        relative_path: str | Path,
        *,
        task_id: str = "",
        scope: DelegationScope | None = None,
        source: str = "interactive",
        requested_by: str = "system",
    ) -> FileResult:
        """Preflight deletion without changing files; grants remain path/task scoped."""
        target = self._safe_target(sandbox, relative_path)
        if target is None:
            return FileResult(path=str(relative_path), wrote=False, reason="Path escapes sandbox")
        kind = self._classify(target, sandbox)
        request = ActionRequest(
            task_id=task_id,
            action=ActionType.FILE_DELETE,
            resource=ResourceRef(
                workspace_id=sandbox.id,
                path=target.as_posix(),
                kind=kind,
            ),
            scope=(scope if scope is not None else DelegationScope()).bind_paths(
                str(self._root(sandbox)),
            ),
        )
        decision = self._policy.decide(request)
        await emit_policy_decision(self._events, request, decision)
        if decision.decision in {AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}:
            ok, value = await self._access(sandbox, target, request, "probe", preflight=True)
            if not ok:
                return FileResult(path=str(relative_path), wrote=False, reason=value,
                                  resource_kind=kind.value)
        if decision.decision is AccessDecision.REQUIRE_APPROVAL and self._approvals is not None:
            args = {"path": target.as_posix(), "sandbox_id": sandbox.id}
            if self._approvals.asks_a_human(source):
                if await self._approvals.is_approved(
                    "file_delete",
                    args,
                    task_id=task_id,
                    source=source,
                    requested_by=requested_by,
                    resource_scope=sandbox.id,
                ):
                    return FileResult(path=str(relative_path), wrote=True, resource_kind=kind.value)
                record = await self._approvals.find_pending("file_delete", args, task_id=task_id)
                if record is None:
                    record = await self._approvals.request(
                        "file_delete",
                        args,
                        task_id=task_id,
                        source=source,
                        requested_by=requested_by,
                        resource_scope=sandbox.id,
                        single_use=False,
                    )
                return FileResult(
                    path=str(relative_path),
                    wrote=False,
                    reason=decision.reason,
                    approval_id=record.id,
                )
        if decision.decision is not AccessDecision.ALLOW:
            return FileResult(
                path=str(relative_path),
                wrote=False,
                reason=decision.reason,
                resource_kind=kind.value,
            )
        return FileResult(path=str(relative_path), wrote=True, resource_kind=kind.value)

    @staticmethod
    def _safe_target(sandbox: SandboxRef, relative_path: str | Path) -> Path | None:
        """Lexical preflight only. Actual identity is established by secure open."""
        if is_device_namespace(relative_path):
            return None
        root = scope_path(str(sandbox.root))
        bound = bound_scope_path(str(relative_path), str(sandbox.root))
        target = scope_path(bound) if bound is not None else None
        if root is None or not root.anchor or target is None or not root.contains(target):
            return None
        # Remote shares are lexical Scope inputs, not an implicitly trusted I/O
        # backend. Reject before any resolver or native open could contact SMB.
        if root.anchor.startswith("//"):
            return None
        return Path(target.text())
