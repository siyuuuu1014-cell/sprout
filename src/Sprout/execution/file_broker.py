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

    async def _node_attribution(self, request, sandbox):
        from Sprout.tools.context_keys import _NODE_TOOL_CALL, _current_node_tool_call

        if _NODE_TOOL_CALL.get() is None:
            return request  # Existing explicit host Broker contract is unchanged.
        frame = _current_node_tool_call()
        if frame is None:
            raise PermissionError("node_file_binding_unavailable")
        return await frame[3].runtime._node_file_request(request, sandbox)

    async def enumerate_readable(
        self, sandbox=None, relative_directory=None, *, subject=None, limits=None,
    ):
        """B1-bound scoped names, no approval consumption or content reads.

        Each visited directory and visible child needs an exact server grant.
        Revocation is checked before accesses and publication, not linearized.
        """
        from dataclasses import replace

        from Sprout.execution.file_access import directory_entries
        from Sprout.execution.file_snapshot import (
            DirectoryEntry,
            DirectoryListingResult,
            DirectoryReadLimits,
            current_read_subject,
            valid_identity,
        )

        limits = limits or DirectoryReadLimits()
        if sandbox is None or relative_directory is None or subject is None:
            return DirectoryListingResult(reason="directory_enumeration_unavailable")
        if (type(limits) is not DirectoryReadLimits
                or any(type(x) is not int or x < 1 for x in
                       (limits.max_entries, limits.max_depth, limits.max_files))
                or limits.max_entries > 10000 or limits.max_depth > 32
                or limits.max_files > 2048):
            return DirectoryListingResult(reason="directory_invalid_budget")
        try:
            target = self._safe_target(sandbox, relative_directory)
            if target is None or type(subject) is not ActionRequest:
                raise PermissionError("directory_subject_unavailable")
            initial = await current_read_subject(str(target))
            if (subject.actor != initial.actor or subject.task_id != initial.task_id
                    or subject.scope != initial.scope or subject.resource != initial.resource):
                raise PermissionError("directory_subject_mismatch")
            from Sprout.tools.context_keys import _current_project_analysis_call

            frame = _current_project_analysis_call()
            if (initial.resource.workspace_id != sandbox.id
                    or self._root(sandbox) != Path(frame.node_scope.workspace.root)):
                raise PermissionError("directory_workspace_mismatch")

            async def authorize(path):
                try:
                    await frame.node_scope.runtime._check_project_selection(
                        (frame, initial, frame.node_scope.workspace), resource_path=str(path),
                    )
                    fresh = await current_read_subject(str(path))
                except PermissionError:
                    return False
                if fresh.actor != initial.actor or fresh.scope != initial.scope:
                    raise PermissionError("directory_authority_changed")
                request = replace(fresh, action=ActionType.DIRECTORY_LIST,
                                  resource=ResourceRef(sandbox.id, path.as_posix(),
                                                       self._classify(path, sandbox)))
                decision = self._policy.decide(request)
                await emit_policy_decision(self._events, request, decision)
                return (decision.decision is AccessDecision.ALLOW
                        and any(rule.startswith("directory:grant:")
                                for rule in decision.matched_rules)
                        and not request.scope.is_expired()
                        and request.scope.permits(request.action.value, path.as_posix()))

            pending, visible, checked, count, files = [(target, 0)], [], [], 0, 0
            async def observe(path):
                nonlocal count
                count += 1
                if count > limits.max_entries:
                    raise PermissionError("directory_budget_exceeded")
                return await authorize(path)

            while pending:
                directory, depth = pending.pop()
                if not await authorize(directory):
                    raise PermissionError("directory_authorization_denied")
                checked.append(directory)
                before_count = count
                entries = await directory_entries(self._root(sandbox), directory,
                                                  limits.max_entries - count, observe)
                if type(entries) is not tuple:
                    raise PermissionError("directory_result_invalid")
                count = max(count, before_count + len(entries))
                if count > limits.max_entries:
                    raise PermissionError("directory_budget_exceeded")
                for entry in entries:
                    if (type(entry) is not DirectoryEntry
                            or type(entry.relative_path) is not str
                            or type(entry.is_directory) is not bool
                            or not valid_identity(entry.identity)):
                        raise PermissionError("directory_result_invalid")
                    path = self._root(sandbox) / entry.relative_path
                    if (self._safe_target(sandbox, entry.relative_path) != path
                            or path.parent != directory
                            or any(item.relative_path == entry.relative_path for item in visible)):
                        raise PermissionError("directory_result_invalid")
                    if not await authorize(path):
                        continue  # No denied name, count or identity is published.
                    checked.append(path)
                    visible.append(entry)
                    if entry.is_directory:
                        if depth >= limits.max_depth:
                            raise PermissionError("directory_budget_exceeded")
                        pending.append((path, depth + 1))
                    else:
                        files += 1
                        if files > limits.max_files:
                            raise PermissionError("directory_budget_exceeded")
            for path in checked:
                if not await authorize(path):
                    raise PermissionError("directory_authorization_changed")
            return DirectoryListingResult(True, entries=tuple(visible))
        except (OSError, ValueError):
            return DirectoryListingResult(reason="directory_enumeration_unavailable")

    async def read_snapshot(
        self, sandbox, relative_path, *, subject: ActionRequest, max_bytes, expected_identity=None,
    ):
        return await self._read_snapshot_impl(sandbox, relative_path, subject=subject,
            max_bytes=max_bytes, expected_identity=expected_identity)

    async def read_worktree_snapshot(self, sandbox, relative_path, *, max_bytes=100_000):
        """F3-only raw bytes: the issuer registry supplies every authority field."""
        from Sprout.execution.verification_manifest import snapshot_subject

        try:
            target = self._safe_target(sandbox, relative_path)
            if target is None:
                raise PermissionError("verification_snapshot_boundary")
            subject = await snapshot_subject(sandbox, str(target))
            return await self._read_snapshot_impl(sandbox, relative_path, subject=subject,
                max_bytes=max_bytes, verification=True)
        except (OSError, ValueError):
            from Sprout.execution.file_snapshot import SnapshotResult

            return SnapshotResult(reason="verification_snapshot_unavailable")

    async def _read_snapshot_impl(self, sandbox, relative_path, *, subject, max_bytes,
                                  expected_identity=None, verification=False):
        """Internal B1-bound single-file capture; no partial result on failure.

        Authority is refreshed before access and before return. This is not a
        linearizable revocation fence. Cancellation joins the worker before
        propagating, so cancelling the await is not mistaken for handle closure.
        """
        from dataclasses import replace
        from threading import Event

        from Sprout.execution.file_snapshot import (
            SnapshotResult,
            _ReadOptions,
            checked_snapshot,
            current_read_subject,
            valid_identity,
        )

        if (type(max_bytes) is not int or not 0 < max_bytes <= 16 * 1024 * 1024
                or (expected_identity is not None
                    and not valid_identity(expected_identity))):
            return SnapshotResult(reason="snapshot_invalid_budget_or_identity")
        stopped = Event()
        try:
            if type(subject) is not ActionRequest:
                return SnapshotResult(reason="snapshot_subject_unavailable")
            target = self._safe_target(sandbox, relative_path)
            if target is None:
                return SnapshotResult(reason="snapshot_boundary_denied")
            async def resolve_subject():
                if verification:
                    from Sprout.execution.verification_manifest import snapshot_subject

                    return await snapshot_subject(sandbox, str(target))
                return await current_read_subject(str(target))

            canonical = await resolve_subject()
            if (subject.actor != canonical.actor or subject.task_id != canonical.task_id
                    or subject.resource != canonical.resource or subject.scope != canonical.scope
                    or subject.action != canonical.action):
                return SnapshotResult(reason="snapshot_subject_mismatch")
            subject = canonical  # Caller fields constrain; only the issuer supplies authority.
            if subject.resource.workspace_id != sandbox.id:
                return SnapshotResult(reason="snapshot_workspace_mismatch")
            # The B1 request has checked the authoritative workspace root/path.
            # Require exactly that root, not a caller-selected broader sandbox.
            from Sprout.tools.context_keys import _current_project_analysis_call

            if not verification:
                frame = _current_project_analysis_call()
                if self._root(sandbox) != Path(frame.node_scope.workspace.root):
                    return SnapshotResult(reason="snapshot_workspace_mismatch")
            kind = self._classify(target, sandbox)
            request = replace(subject, action=ActionType.FILE_READ,
                              resource=ResourceRef(workspace_id=sandbox.id,
                                                   path=target.as_posix(), kind=kind))
            if kind not in {ResourceKind.SOURCE, ResourceKind.TEST, ResourceKind.CONFIG,
                            ResourceKind.PUBLIC, ResourceKind.DOCUMENTATION}:
                await emit_policy_decision(self._events, request, PolicyDecision(
                    AccessDecision.DENY, "snapshot_classification_denied",
                    matched_rules=("broker:snapshot:classification",),
                ))
                return SnapshotResult(reason="snapshot_classification_denied")

            def evaluate(req):
                decision = self._policy.decide(req)
                if (req.scope.is_expired()
                        or not req.scope.permits(ActionType.FILE_READ.value, req.resource.path)):
                    return PolicyDecision(
                        AccessDecision.DENY, "DelegationScope expired or outside resource boundary",
                        matched_rules=("scope:expired" if req.scope.is_expired()
                                       else "scope:denied",),
                    )
                return decision

            async def decide(req):
                decision = evaluate(req)
                await emit_policy_decision(self._events, req, decision)
                if (decision.decision is not AccessDecision.ALLOW
                        or req.scope.is_expired()
                        or not req.scope.permits(ActionType.FILE_READ.value, req.resource.path)):
                    raise PermissionError("snapshot_authorization_denied")

            await decide(request)
            final_decisions = []

            def final_authorize():
                decision = evaluate(request)
                final_decisions.append(decision)
                if (stopped.is_set() or decision.decision is not AccessDecision.ALLOW
                        or request.scope.is_expired()
                        or not request.scope.permits(
                            ActionType.FILE_READ.value, target.as_posix())):
                    raise FileBoundaryError("snapshot_authorization_denied")

            worker = asyncio.create_task(asyncio.to_thread(
                file_operation, self._root(sandbox), target, "snapshot", final_authorize,
                content=_ReadOptions(max_bytes, expected_identity, stopped, final_authorize),
            ))
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                stopped.set()
                # Repeated cancellation must not abandon a live fd-owning worker.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except (OSError, ValueError):
                        break
                if worker.done() and not worker.cancelled():
                    worker.exception()
                raise
            finally:
                if final_decisions:
                    await emit_policy_decision(self._events, request, final_decisions[-1])
            result = checked_snapshot(result, max_bytes, expected_identity)
            if not result.ok:
                return result
            latest = await resolve_subject()
            if (latest.actor != subject.actor or latest.task_id != subject.task_id
                    or latest.resource != subject.resource or latest.scope != subject.scope):
                return SnapshotResult(reason="snapshot_authority_changed")
            await decide(replace(request, scope=latest.scope, actor=latest.actor))
            return result
        except (OSError, ValueError):
            return SnapshotResult(reason="snapshot_unavailable")
        finally:
            stopped.set()

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
        request = await self._node_attribution(request, sandbox)
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
        actor=None,
    ) -> FileResult:
        target = self._safe_target(sandbox, relative_path)
        if target is None:
            return FileResult(path=str(relative_path), wrote=False, reason="Path escapes sandbox")

        kind = self._classify(target, sandbox)
        request = ActionRequest(
            task_id=task_id,
            **({"actor": actor} if actor is not None else {}),
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
        request = await self._node_attribution(request, sandbox)
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
