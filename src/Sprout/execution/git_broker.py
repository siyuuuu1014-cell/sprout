"""Policy-controlled Git operations."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from Sprout.execution import git_s1
from Sprout.execution.models import GitResult
from Sprout.execution.policy_emit import emit_policy_decision
from Sprout.security.access import AccessDecision, ActionRequest, ActionType, PolicyDecision
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.task.models import DelegationScope
from Sprout.workspace.models import ResourceKind, ResourceRef, Workspace

if TYPE_CHECKING:
    from Sprout.events import EventBus

#: Legacy API constant, not authority to revive retired generic operations.
GIT_WRITE_TOOL = "git_write"


async def run_capture(*args: object, **kwargs: object) -> tuple[int, str, str]:
    """Legacy module symbol retained for observers; no longer an I/O executor."""
    raise RuntimeError("Generic Git execution is retired")


class GitBroker:
    """S1-gated reads and explicit refusal of retired generic capabilities."""

    def __init__(
        self,
        policy: PolicyEngine,
        *,
        approvals: ApprovalManager | None = None,
        events: EventBus | None = None,
    ) -> None:
        self._policy = policy
        self._approvals = approvals
        self._events = events

    async def status(
        self, workspace: Workspace, *, scope: DelegationScope | None = None
    ) -> GitResult:
        decision = await self._decide(workspace, ActionType.GIT_STATUS, {}, "", scope)
        if decision is not AccessDecision.ALLOW:
            return GitResult(action="status", allowed=False, reason=decision.value)
        return await git_s1.run_read(workspace, "status", scope)

    async def diff(
        self, workspace: Workspace, *, scope: DelegationScope | None = None
    ) -> GitResult:
        decision = await self._decide(workspace, ActionType.GIT_DIFF, {}, "", scope)
        if decision is not AccessDecision.ALLOW:
            return GitResult(action="diff", allowed=False, reason=decision.value)
        return await git_s1.run_read(workspace, "diff", scope)

    async def log(self, workspace: Workspace, limit: int = 20) -> GitResult:
        return await self._refuse(workspace, ActionType.GIT_LOG, {"limit": limit})

    async def branch(self, workspace: Workspace) -> GitResult:
        return await self._refuse(workspace, ActionType.GIT_BRANCH, {})

    async def add(self, workspace: Workspace, files: tuple[str, ...] = ()) -> GitResult:
        return await self._refuse(workspace, ActionType.GIT_ADD, {"files": list(files)})

    async def commit(
        self,
        workspace: Workspace,
        message: str,
        *,
        files: tuple[str, ...] = (),
        task_id: str = "",
        scope: DelegationScope | None = None,
    ) -> GitResult:
        arguments = {"message": message, "files": list(files)}
        return await self._refuse(workspace, ActionType.GIT_COMMIT, arguments, task_id, scope)

    async def push(
        self,
        workspace: Workspace,
        remote: str = "origin",
        branch: str | None = None,
        *,
        task_id: str = "",
        scope: DelegationScope | None = None,
    ) -> GitResult:
        arguments = {"remote": remote, "branch": branch}
        return await self._refuse(workspace, ActionType.GIT_PUSH, arguments, task_id, scope)

    async def pull(
        self,
        workspace: Workspace,
        remote: str = "origin",
        branch: str | None = None,
        *,
        task_id: str = "",
    ) -> GitResult:
        arguments = {"remote": remote, "branch": branch}
        return await self._refuse(workspace, ActionType.GIT_PULL, arguments, task_id)

    async def _refuse(
        self, workspace: Workspace, action: ActionType, arguments: dict,
        task_id: str = "", scope: DelegationScope | None = None,
    ) -> GitResult:
        decision = await self._decision(workspace, action, arguments, task_id, scope, retired=True)
        return GitResult(action=action.value.removeprefix("git."), allowed=False,
                         reason=decision.reason or decision.decision.value)

    async def _decide(
        self,
        workspace: Workspace,
        action: ActionType,
        arguments: dict,
        task_id: str = "",
        scope: DelegationScope | None = None,
    ) -> AccessDecision:
        return (await self._decision(workspace, action, arguments, task_id, scope)).decision

    async def _decision(
        self, workspace: Workspace, action: ActionType, arguments: dict,
        task_id: str = "", scope: DelegationScope | None = None, *, retired: bool = False,
    ) -> PolicyDecision:
        resource = ResourceRef(
            workspace_id=workspace.id,
            path=str(workspace.root),
            kind=ResourceKind.CONFIG,
        )
        request = ActionRequest(
            task_id=task_id,
            action=action,
            resource=resource,
            arguments=arguments,
            scope=scope if scope is not None else DelegationScope(),
        )
        decision = self._policy.decide(request)
        if retired and decision.decision in {AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}:
            decision = replace(
                decision, decision=AccessDecision.DENY,
                reason="Generic GitBroker capability retired",
                matched_rules=(*decision.matched_rules, "capability:git:retired"),
            )
        await emit_policy_decision(self._events, request, decision)
        return decision

    @staticmethod
    async def _run(workspace: Workspace, *args: str) -> str:
        raise RuntimeError("Generic Git execution is retired")
