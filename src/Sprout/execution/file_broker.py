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

from Sprout.execution.models import FileResult, SandboxRef
from Sprout.execution.policy_emit import emit_policy_decision
from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.security.engine import PolicyEngine
from Sprout.task.models import DelegationScope
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
            target,
            workspace_root=sandbox.root,
            overrides=self._classify_overrides,
        )

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
            scope=scope if scope is not None else DelegationScope(),
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

        await asyncio.to_thread(self._write, target, content,
                                **({"newline": newline} if newline is not None else {}))
        return FileResult(path=str(relative_path), wrote=True, resource_kind=kind.value)

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
            scope=scope if scope is not None else DelegationScope(),
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

        content = await asyncio.to_thread(self._read, target)
        return FileResult(
            path=str(relative_path),
            wrote=True,
            resource_kind=kind.value,
            content=content,
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
        return await asyncio.to_thread(Path.exists, target)

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
        await asyncio.to_thread(self._delete, target)
        return result

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
            scope=scope if scope is not None else DelegationScope(),
        )
        decision = self._policy.decide(request)
        await emit_policy_decision(self._events, request, decision)
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
        """Resolve a sandbox-relative path, refusing device-namespace input first."""
        if is_device_namespace(relative_path):
            return None
        root = Path(sandbox.root).resolve()
        target = (root / relative_path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return target

    @staticmethod
    def _write(path: Path, content: str, *, newline: str | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline=newline)

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except OSError as exc:
            return f"read failed: {exc}"

    @staticmethod
    def _delete(path: Path) -> None:
        path.unlink(missing_ok=True)
