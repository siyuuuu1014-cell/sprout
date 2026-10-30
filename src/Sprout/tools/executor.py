"""Security-gated tool execution (AUTHZ §1.1, §5.3).

The tool path speaks the same vocabulary as the brokers now: ``SecurityPolicy``
returns a :class:`~Sprout.security.access.PolicyDecision` (with a ``risk:*``
provenance rule) instead of its own enum, and every decision can be written to
the security audit stream.

Approvals are graded by where the call came from: an interactive session parks
and asks a human, while a cron/automation session is denied outright because
nobody is watching (Hermes ``cron_mode: deny``).

Fail-safe rule: when a call requires approval but no approval manager is
wired, the call is denied rather than executed.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from Sprout.events import (
    APPROVAL_REQUESTED,
    TOOL_DENIED,
    TOOL_EXECUTED,
    Event,
    EventBus,
)
from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.security.approval import ApprovalManager
from Sprout.security.layered_policy import LayeredPolicyEngine
from Sprout.security.policy import Decision, SecurityPolicy
from Sprout.security.protocol import PolicyEngineProtocol
from Sprout.tools.context_keys import (
    CONTEXT_KEY,
    CONTEXT_TOOLS,
    NodeToolInvocationContext,
    _approved_cli_call,
    _project_analysis_call,
)
from Sprout.tools.project_analyze_tool import ProjectAnalyzeTool
from Sprout.tools.registry import ToolRegistry
from Sprout.tools.result import ToolExecutionUnconfirmed, ToolResult
from Sprout.tools.workspace_query_tool import WorkspaceQueryTool

if TYPE_CHECKING:
    from Sprout.security.audit import SecurityAuditLog

__all__ = ["Decision", "ToolExecutor"]


@dataclass(slots=True)
class ToolExecutor:
    """Runs one tool call through the security policy before invoking it.

    ``scoped`` carries the tools a single node was given. Node tools take
    precedence over ``tools``, which stays the process-wide registry for the
    security floor's risk lookup and the ``tool_run`` meta-tool. Keeping node
    tools in a per-run overlay instead of registering them globally is what
    lets concurrent nodes each run with their own set — the previous
    register/unregister pair was a race, not just untidy.
    """

    tools: ToolRegistry
    policy: SecurityPolicy = field(default_factory=SecurityPolicy)
    approvals: ApprovalManager | None = None
    events: EventBus | None = None
    audit: SecurityAuditLog | None = None
    #: Server-side command facts, used to recognise a read-only ``cli_tool_run``
    #: and skip the prompt for it. ``None`` keeps the old behaviour: every
    #: ``cli_tool_run`` asks.
    commands: Any | None = None
    #: Node-scoped tools consulted before ``tools``. ``None`` means "no node
    #: scope", which is the interactive path.
    scoped: Mapping[str, Any] | None = None
    #: Strict model contexts cannot dispatch outside their explicit tool set.
    scoped_only: bool = False
    policy_engine: PolicyEngineProtocol = field(default_factory=LayeredPolicyEngine)

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        requested_by: str = "unknown",
        task_id: str = "",
        session_id: str = "",
        source: str = "interactive",
        resource_scope: str = "",
    ) -> ToolResult:
        # Freeze CLI input before any policy/approval awaits; the caller cannot
        # mutate the approved argv/env while the grant is being consumed.
        if name == "cli_tool_run":
            arguments = deepcopy(dict(arguments))
        try:
            tool = self._resolve(name)
        except KeyError:
            return ToolResult.failure(f"Unknown tool: {name}")
        except LookupError as exc:
            return ToolResult.failure(str(exc))

        if isinstance(tool, (ProjectAnalyzeTool, WorkspaceQueryTool)):
            # Policy, approval and invoke must see the same caller-independent
            # probe flag and payload, including across audit/approval awaits.
            arguments = deepcopy(dict(arguments))
            with _project_analysis_call(tool) as frame:
                return await self._execute_tool(
                    name, tool, arguments, requested_by=requested_by, task_id=task_id,
                    session_id=session_id, source=source, resource_scope=resource_scope,
                    analysis_frame=frame,
                )
        return await self._execute_tool(
            name, tool, arguments, requested_by=requested_by, task_id=task_id,
            session_id=session_id, source=source, resource_scope=resource_scope,
        )

    async def _execute_tool(
        self, name, tool, arguments, *, requested_by, task_id, session_id, source,
        resource_scope, analysis_frame=None,
    ):
        from Sprout.tools.context_keys import _current_project_node

        scope = _current_project_node()
        bound = getattr(tool, "invocation_context", None)
        if isinstance(bound, NodeToolInvocationContext):
            if not self.scoped_only:
                return ToolResult.denied("Bound node tools require strict node-local dispatch")
            task_id, source = bound.task_id, bound.source
            requested_by, resource_scope = bound.requested_by, bound.resource_scope

        from Sprout.mcp.adapter.tool import MCPTool, _authorized_mcp_call, _classification

        is_mcp = isinstance(tool, MCPTool)
        from Sprout.tools.context_keys import _PROJECT_NODE

        generic_binding = not is_mcp and analysis_frame is None and (
            _PROJECT_NODE.get() is not None or bool(getattr(tool, "_task_id", "")))
        if generic_binding:
            arguments = deepcopy(dict(arguments))
        if is_mcp:
            arguments = deepcopy(dict(arguments))
        decision = self.policy.decide_for_spec(tool.spec)
        subject = None
        binding_denied = False
        binding_rejected = False
        if generic_binding:
            try:
                if scope is None or not self.scoped_only:
                    raise PermissionError("node_tool_binding_unavailable")
                subject = await scope.runtime._node_tool_request(tool, arguments)
                task_id, source = subject.task_id, scope.task.source
                session_id, requested_by = scope.session_id, subject.actor.user_id
                resource_scope = scope.workspace.id
            except Exception:  # noqa: BLE001 - invalid node authority never falls back
                binding_denied = True
        elif analysis_frame is not None and analysis_frame.node_scope is not None:
            try:
                if not self.scoped_only:
                    raise PermissionError("Strict node dispatch required")
                subject = await analysis_frame.node_scope.runtime._project_analysis_request(
                    tool, arguments, analysis_frame, session_id,
                )
                task_id, source = subject.task_id, analysis_frame.node_scope.task.source
                requested_by, resource_scope = subject.actor.user_id, subject.resource.workspace_id
            except Exception:  # noqa: BLE001 - metadata failure cannot grant a default scope
                binding_denied = True
        elif is_mcp:
            from Sprout.tools.context_keys import _PROJECT_NODE

            # A copied/expired frame must not fall back to a host MCP call.
            if scope is None and _PROJECT_NODE.get() is not None:
                binding_denied = True
            elif scope is not None:
                try:
                    if not self.scoped_only:
                        raise PermissionError("Strict node dispatch required")
                    task, workspace = await scope.runtime._project_analysis_subject(scope)
                    subject = ActionRequest(
                        task_id=task.id, actor=task.actor, action=ActionType.TOOL_INVOKE,
                        scope=task.delegation_scope.bind_paths(str(workspace.root)).narrow(
                            scope.task.delegation_scope.bind_paths(str(workspace.root))),
                    )
                    task_id, source = task.id, task.source
                    session_id = scope.session_id
                    requested_by, resource_scope = task.actor.user_id, workspace.id
                except Exception:  # noqa: BLE001 - no default identity on failed revalidation
                    binding_denied = True

        # A read-only command is granted without asking. This runs *after* the
        # policy decision and only ever relaxes REQUIRE_APPROVAL — a DENY still
        # denies, and the check is on the arguments (the actual argv) rather
        # than on the tool, because ``cli_tool_run`` is one tool for both
        # ``git status`` and ``git push``.
        read_only = decision.decision is AccessDecision.REQUIRE_APPROVAL and (
            self._is_read_only_invocation(name, arguments)
        )
        if read_only:
            decision = replace(
                decision,
                decision=AccessDecision.ALLOW,
                reason=f"{decision.reason}; read-only invocation",
                matched_rules=(*decision.matched_rules, "readonly:cli_tool_run"),
            )

        policy_arguments = {**arguments, "tool_name": tool.spec.name}
        command = arguments.get("command")
        argv = arguments.get("args")
        if isinstance(command, str) and isinstance(argv, list | tuple):
            policy_arguments["command"] = [command, *argv]
        # An invalid B1 subject cannot be replaced by an unrestricted request.
        # Keep the ToolSpec DENY and PA-1 precedence; otherwise deny the binding
        # below. Only validated B1 subjects reach resource/Scope policy checks.
        if not binding_denied:
            boundary_request = (replace(subject, arguments=policy_arguments)
                                if subject is not None else ActionRequest(
                                    task_id=task_id, action=ActionType.TOOL_INVOKE,
                                    arguments=policy_arguments,
                                ))
            boundary = self.policy_engine.decide(boundary_request)
            decision = replace(
                decision,
                decision=LayeredPolicyEngine._intersect(decision.decision, boundary.decision),
                reason="; ".join(filter(None, (boundary.reason, decision.reason))),
                matched_rules=(*boundary.matched_rules, *decision.matched_rules),
            )

        if ((is_mcp or generic_binding) and binding_denied
                and decision.decision in {AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}):
            binding_rejected = True
            decision = replace(decision, decision=AccessDecision.DENY,
                reason="mcp_task_binding_denied" if is_mcp else "node_tool_binding_denied",
                matched_rules=(*decision.matched_rules, "binding:task:denied"))

        from Sprout.tools.system_tools import CliDownloadTool, GitWriteTool, SkillScriptTool

        retired = type(tool) is GitWriteTool and decision.decision in {
            AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL,
        }
        if retired:
            decision = replace(
                decision, decision=AccessDecision.DENY,
                reason="Generic GitWriteTool capability retired",
                matched_rules=(*decision.matched_rules, "capability:git_write:retired"),
            )

        mcp_binding = _classification(tool) if is_mcp else None
        unclassified_mcp = is_mcp and mcp_binding is None and decision.decision in {
            AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL,
        }
        if unclassified_mcp:
            decision = replace(
                decision, decision=AccessDecision.DENY,
                reason="MCP operation has no current trusted risk classification",
                matched_rules=(*decision.matched_rules, "mcp:unclassified"),
            )

        # Negative capability: the disabled built-in type and all subclasses
        # remain unavailable, even if invoke is overridden or registered via an
        # overlay/another registry. Unrelated same-name plugins are unaffected.
        # Preserve Policy/Scope denial and reject before touching approvals.
        unavailable = "skill_script_execution_unavailable" if (
            isinstance(tool, SkillScriptTool) and decision.decision in {
            AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL,
            }
        ) else ""
        if isinstance(tool, CliDownloadTool) and decision.decision in {
                AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}:
            unavailable = "download_file_write_contract_unavailable"
        if unavailable:
            decision = replace(
                decision, decision=AccessDecision.DENY, reason=unavailable,
                matched_rules=(*decision.matched_rules, "capability:skill_script:unavailable"),
            )

        if (isinstance(tool, ProjectAnalyzeTool)
                and arguments.get("include_system_tools") is True
                and decision.decision in {AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}):
            unavailable = "native_project_probe_unavailable"
            decision = replace(
                decision, decision=AccessDecision.DENY, reason=unavailable,
                matched_rules=(*decision.matched_rules, "capability:project_probe:unavailable"),
            )

        if (isinstance(tool, ProjectAnalyzeTool)
                and decision.decision in {AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}):
            if binding_denied:
                binding_rejected = True
                decision = replace(
                    decision, decision=AccessDecision.DENY,
                    reason="project_analysis_binding_denied",
                    matched_rules=(*decision.matched_rules, "binding:project_analysis:denied"),
                )

        ready = (analysis_frame is not None and analysis_frame.node_scope is not None
                 and analysis_frame.node_scope.runtime._project_tool_ready(
                     tool, arguments, analysis_frame))
        if (isinstance(tool, ProjectAnalyzeTool)
                and (not ready or decision.decision is AccessDecision.REQUIRE_APPROVAL)
                and decision.decision in {AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}):
            unavailable = "project_analysis_scope_unavailable"
            decision = replace(
                decision, decision=AccessDecision.DENY, reason=unavailable,
                matched_rules=(*decision.matched_rules, "capability:project_analysis:unavailable"),
            )

        if (isinstance(tool, WorkspaceQueryTool)
                and (binding_denied or not ready
                     or decision.decision is AccessDecision.REQUIRE_APPROVAL)
                and decision.decision in {AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}):
            unavailable = "workspace_query_scope_unavailable"
            decision = replace(
                decision, decision=AccessDecision.DENY, reason=unavailable,
                matched_rules=(*decision.matched_rules, "capability:workspace_query:unavailable"),
            )

        await self._audit(name, task_id, source, decision.decision.value, decision.reason)

        if decision.decision not in {AccessDecision.ALLOW, AccessDecision.REQUIRE_APPROVAL}:
            await self._publish(TOOL_DENIED, {
                "tool": name, "reason": ("capability_retired" if retired else
                                         "capability_unavailable" if unavailable else
                                         "binding" if binding_rejected else
                                         "mcp_unclassified" if unclassified_mcp else "policy"),
            })
            if retired or unclassified_mcp or unavailable or binding_rejected:
                return ToolResult.denied(decision.reason)
            return ToolResult.denied(f"Tool {name} is denied by the security policy")

        approval_granted = False
        approval_arguments = ({"mcp_classification_v1": mcp_binding, "arguments": arguments}
                              if is_mcp else arguments)
        if decision.decision is AccessDecision.REQUIRE_APPROVAL:
            if self.approvals is None:
                await self._publish(TOOL_DENIED, {"tool": name, "reason": "no-approval-manager"})
                return ToolResult.denied(
                    f"Tool {name} requires approval, but no approval manager is configured"
                )
            mode = self.approvals.mode_for(source)
            if not self.approvals.asks_a_human(source):
                # cron / automation: nobody is watching, so no human decision
                # can arrive. Fail closed instead of parking the task forever.
                await self._publish(
                    TOOL_DENIED,
                    {"tool": name, "reason": "unattended", "mode": mode.value},
                )
                return ToolResult.denied(
                    f"Tool {name} requires approval and source {source!r} is "
                    f"unattended (mode: {mode.value})"
                )
            approval_granted = await self.approvals.is_approved(
                name,
                approval_arguments,
                task_id=task_id,
                session_id=session_id,
                requested_by=requested_by,
                source=source,
                resource_scope=resource_scope,
            )
            if not approval_granted:
                record = await self.approvals.request(
                    name,
                    approval_arguments,
                    task_id=task_id,
                    requested_by=requested_by,
                    resource_scope=resource_scope,
                    source=source,
                    session_id=session_id,
                )
                await self._publish(
                    APPROVAL_REQUESTED, {"tool": name, "approval_id": record.id}
                )
                return ToolResult.needs_approval(record.id)

        try:
            contextual = self._with_context(
                name,
                arguments,
                task_id,
                session_id,
                source,
                requested_by,
                approval_granted=approval_granted,
                resource_scope=resource_scope,
            )
            from Sprout.tools.system_tools import CliRunTool

            handoff = nullcontext()
            if approval_granted and type(tool) is CliRunTool:
                binding = tool._approval_binding(
                    contextual, task_id, session_id, source, requested_by, resource_scope,
                )
                if binding is not None:
                    handoff = _approved_cli_call(tool._process_broker, self.approvals, binding)
            if is_mcp:
                handoff = _authorized_mcp_call(
                    tool, contextual, (task_id, session_id, source, requested_by, resource_scope),
                    mcp_binding,
                )
            if generic_binding:
                from Sprout.tools.context_keys import _node_tool_call

                fresh = await scope.runtime._node_tool_request(tool, arguments)
                if replace(fresh, id=subject.id, created_at=subject.created_at) != subject:
                    raise PermissionError("node_tool_authority_changed")
                handoff = _node_tool_call(tool, subject, arguments)
            previous_untracked = scope.untracked_tool_use if scope is not None else False
            if scope is not None and not (
                analysis_frame is not None
                and scope.runtime._project_tool_ready(tool, arguments, analysis_frame)
            ):
                # Preserve the negative marker unless an exact admitted READ
                # is proven below. Exceptions leave the marker set.
                scope.untracked_tool_use = True
            with handoff:
                result = await tool.invoke(contextual)
            if generic_binding and scope.runtime._same_admitted_read(
                    scope, tool, arguments, result):
                scope.untracked_tool_use = previous_untracked
            if generic_binding and self.audit is not None:
                from hashlib import sha256

                self.audit.record("tool.result", {
                    "tool": name, "task_id": subject.task_id, "node_id": scope.node.id,
                    "actor": subject.actor.user_id, "source": source, "ok": result.ok,
                    "content_sha256": sha256(result.content.encode()).hexdigest(),
                })
        except ToolExecutionUnconfirmed:
            await self._publish(TOOL_EXECUTED, {"tool": name, "ok": False,
                                                "execution_unconfirmed": True})
            raise
        except Exception as exc:  # noqa: BLE001 - tool errors must not break the loop
            await self._publish(TOOL_EXECUTED, {"tool": name, "ok": False})
            return ToolResult.failure(f"{type(exc).__name__}: {exc}")
        await self._publish(TOOL_EXECUTED, {"tool": name, "ok": result.ok})
        return result

    def _is_read_only_invocation(self, name: str, arguments: Mapping[str, Any]) -> bool:
        """True when this call is a command that only reads.

        Only ``cli_tool_run`` is considered, and only when a command registry is
        wired. Everything else keeps the policy decision it was given — the
        point is to stop re-asking about ``pwd`` and ``git status``, not to
        widen what the runtime will run.
        """
        if self.commands is None or name != "cli_tool_run":
            return False
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return False
        raw_args = arguments.get("args") or []
        if not isinstance(raw_args, (list, tuple)):
            return False
        argv = [command, *(str(item) for item in raw_args)]
        from Sprout.security.commands import is_read_only_command

        try:
            return is_read_only_command(argv, registry=self.commands)
        except Exception:  # noqa: BLE001 - a classification bug must not grant
            return False

    def _with_context(
        self,
        name: str,
        arguments: Mapping[str, Any],
        task_id: str,
        session_id: str,
        source: str,
        requested_by: str,
        *,
        approval_granted: bool = False,
        resource_scope: str = "",
    ) -> Mapping[str, Any]:
        """Attach the calling context a tool cannot otherwise see.

        A tool only receives its ``arguments`` (``Tool.invoke``), so a tool that
        delegates onward — ``cli_tool_run`` handing a command to
        ``ProcessBroker`` — has no way to tell the next layer *which task* and
        *which source* it is acting for. The broker then evaluates its own
        ``process_run`` gate with an empty task id, cannot match the grant the
        operator just issued for the outermost tool, and refuses a call that was
        already approved.

        Injected **after** the decision and fingerprinting above, under a
        reserved key tools are expected to strip rather than forward into their
        own policy arguments: including it in a fingerprint would make one
        approval stop covering the same call replayed in a new turn.
        """
        if name not in CONTEXT_TOOLS:
            return arguments
        return {
            **arguments,
            CONTEXT_KEY: {
                "task_id": task_id,
                "session_id": session_id,
                "source": source,
                "requested_by": requested_by,
                "approval_granted": approval_granted,
                "resource_scope": resource_scope,
            },
        }

    def _resolve(self, name: str) -> Any:
        """Resolve the overlay, rejecting registry fallback for strict contexts."""
        if self.scoped is not None and name in self.scoped:
            return self.scoped[name]
        if self.scoped_only:
            raise KeyError(name)
        return self.tools.get(name)

    def with_scoped_tools(
        self, tools: Mapping[str, Any], *, strict: bool = False,
    ) -> ToolExecutor:
        """A copy dispatching to ``tools`` for one node's run.

        A copy rather than a mutation: concurrent nodes must not see each
        other's tool set. ``strict`` makes the overlay an allowlist; even an
        empty overlay then blocks process-wide tools.
        """
        clone = replace(self, scoped=dict(tools), scoped_only=strict)
        return clone

    async def _audit(
        self, tool: str, task_id: str, source: str, decision: str, reason: str
    ) -> None:
        if self.audit is None:
            return
        from Sprout.security.audit import KIND_POLICY_DECISION

        self.audit.record(
            KIND_POLICY_DECISION,
            {
                "action": "tool.invoke",
                "resource": tool,
                "tool": tool,
                "task_id": task_id,
                "source": source,
                "decision": decision,
                "reason": reason,
            },
        )

    async def _publish(self, name: str, payload: dict[str, Any]) -> None:
        if self.events is not None:
            await self.events.publish(Event(name, payload))


# Re-exported so callers that still import the tool-risk vocabulary keep working.
