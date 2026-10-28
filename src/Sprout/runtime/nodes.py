"""Node execution: one class owning every execution-graph node branch.

The Runtime builds a :class:`NodeExecutor` and hands the orchestrator a handler
closure, so the graph runner never sees brokers, prompts, or output clamping
(Herness spec 7.2 for the minimal node context, 8.3 for approval parking).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Awaitable, Callable, Mapping
from contextlib import nullcontext
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from Sprout.agent.router import AgentRouter
from Sprout.artifacts.models import ArtifactKind
from Sprout.context.builder import ContextBuilder
from Sprout.execution.acceptance import (
    acceptance_commands,
    acceptance_demo_path,
    acceptance_test_path,
    local_acceptance_evidence,
    local_evidence_applicability,
    local_executor_applicability,
    local_executor_binding,
    render_acceptance_demo,
    render_acceptance_tests,
    verification_evidence_applicability,
)
from Sprout.execution.apply import ApplyBroker
from Sprout.execution.change import ChangeProposalBuilder
from Sprout.execution.coding_command_tool import IsolatedCodingCommandTool
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.isolated_auxiliary import auxiliary_commands
from Sprout.execution.isolated_model_tools import (
    IsolatedModelPathGuard,
    IsolatedModelTool,
    public_acceptance,
)
from Sprout.execution.models import (
    ChangeProposalStatus,
    ProcessResult,
    SandboxRef,
    TestResult,
)
from Sprout.execution.process_broker import ProcessBroker
from Sprout.execution.sandbox_tool import (
    SandboxApplyPatchTool,
    SandboxEditTool,
    SandboxGitTool,
    SandboxListTool,
    SandboxReadTool,
    SandboxSearchTool,
    SandboxWriteTool,
)
from Sprout.execution.verification import (
    build_verification_report,
    classify_verification_command,
    proposal_verification_blocker,
)
from Sprout.llm.language import detect_language
from Sprout.message.models import Message
from Sprout.orchestration.models import ExecutionNode, NodeType
from Sprout.sandbox.git_worktree import GitWorktreeSandbox
from Sprout.sandbox.isolated_git_worktree import IsolatedGitWorktreeSandbox
from Sprout.skills.models import Skill
from Sprout.skills.registry import SkillRegistry
from Sprout.skills.tools import SkillViewTool
from Sprout.storage.contracts.metadata import MetadataStore
from Sprout.strategy.acceptance import AcceptanceSpec, acceptance_prompt
from Sprout.strategy.model_planner import ModelStrategyPlanner
from Sprout.task.models import Task
from Sprout.tools.registry import ToolRegistry
from Sprout.trajectory.models import ArtifactSnapshot, TrajectoryEvent
from Sprout.trajectory.recorder import JsonlTrajectoryRecorder
from Sprout.workspace.models import ReadPlan, Workspace, WorkspaceKind
from Sprout.workspace.read_broker import ReadBroker

# Clamping keeps node metadata small and avoids persisting whole files.
_NODE_OUTPUT_LIMIT = 4_000
_RESOURCE_PREVIEW_CHARS = 1_500
# Verification output is persisted into node metadata, so it is clamped harder
# than the agent's own answer.
_TEST_OUTPUT_CHARS = 2_000


def verification_status(*, executed: int, failed: int, withheld: int) -> str:
    """Classify an evaluation round from its command outcomes.

    Three outcomes, not two. A command the policy withheld never ran, so
    calling it a failure says something false about the change — and it hid the
    fact that evaluation was executing nothing at all while reporting "failed"
    every round, which also made the verify-loop retry a condition that
    retrying cannot change (another agent round does not grant approval).

    A real failure still outranks a withheld command: that change is known bad,
    whatever else did not get to run.
    """
    if executed == 0:
        return "no_test_commands"
    if withheld == executed:
        return "not_verified"
    if failed:
        return "failed"
    if withheld:
        # Some passed, some never ran: those passes are not a verified change.
        return "partially_verified"
    return "passed"


def _clamp_test_output(stdout: str, stderr: str, error: str | None) -> str:
    """Build one bounded output blob for a persisted test/build result."""
    blocks: list[str] = []
    if stdout.strip():
        blocks.append(stdout.strip())
    if stderr.strip():
        blocks.append("[stderr]\n" + stderr.strip())
    if not blocks and error:
        blocks.append(error)
    return "\n".join(blocks)[:_TEST_OUTPUT_CHARS]


def _test_result_payload(result: TestResult) -> dict[str, Any]:
    """JSON-safe shape; node metadata is persisted, so no dataclasses travel."""
    return {
        "name": result.name,
        "passed": result.passed,
        "output": result.output,
        "duration_ms": result.duration_ms,
        # Round-trips, or every reconstructed result would default to
        # "executed" and the apply gate would lose the distinction again.
        "executed": result.executed,
    }


def _test_result_from_payload(payload: Mapping[str, Any]) -> TestResult:
    try:
        duration = float(payload.get("duration_ms") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return TestResult(
        name=str(payload.get("name", "")),
        passed=bool(payload.get("passed")),
        output=str(payload.get("output", "")),
        duration_ms=duration,
        # Absent means an older payload, which predates withheld commands and
        # therefore always executed.
        executed=bool(payload.get("executed", True)),
    )


class NodeExecutor:
    """Executes one node: reads resources, runs the agent, gates, applies.

    The executor owns the node-type branches and the prompts handed to the
    agent; the Runtime only assembles it and forwards a handler to the
    orchestrator.
    """

    def __init__(
        self,
        *,
        metadata: MetadataStore,
        router: AgentRouter | None,
        contexts: ContextBuilder,
        tools: ToolRegistry,
        skills: SkillRegistry,
        read_broker: ReadBroker,
        file_broker: FileBroker,
        process_broker: ProcessBroker,
        apply_broker: ApplyBroker,
        changes: Any | None = None,
        coding_dispatch=None,
        isolated_coding_required: bool = False,
        coding_session_factory=None,
        coding_commands=None,
        local_acceptance: bool = True,
        project_analysis_scope=None,
    ) -> None:
        self._metadata = metadata
        self._router = router
        self._contexts = contexts
        self._tools = tools
        self._skills = skills
        self._read_broker = read_broker
        self._file_broker = file_broker
        self._process_broker = process_broker
        self._apply_broker = apply_broker
        self._changes = changes
        self._coding_dispatch = coding_dispatch
        self._isolated_coding_required = isolated_coding_required
        self._coding_session_factory = coding_session_factory
        self._coding_commands = coding_commands
        self._local_acceptance = local_acceptance
        self._project_analysis_scope = project_analysis_scope
        self._change_builder = ChangeProposalBuilder()

    def handler(
        self, task: Task, workspace: Workspace, read_plan: ReadPlan
    ) -> Callable[[ExecutionNode], Awaitable[Any]]:
        """Build the callable the orchestrator invokes per node."""

        async def execute(node: ExecutionNode) -> Any:
            current = task
            if self._project_analysis_scope is not None:
                # Local scheduling changes CREATED/READY to RUNNING before
                # dispatch. Re-read that authority instead of binding the
                # pre-submission snapshot as the producer of every result.
                current = await self._metadata.get_task(task.id)
                registered = await self._metadata.get_workspace(workspace.id)
                if (current is None or registered is None
                        or current.workspace_id != task.workspace_id
                        or current.actor != task.actor or current.source != task.source
                        or current.delegation_scope != task.delegation_scope
                        or current.metadata.get("session_id") != task.metadata.get("session_id")
                        or (registered.id, registered.root, registered.kind, registered.revision)
                        != (workspace.id, workspace.root, workspace.kind, workspace.revision)):
                    raise PermissionError("node_task_subject_changed")
            return await self.execute_node(node, workspace, read_plan, current)

        return execute

    async def execute_node(
        self,
        node: ExecutionNode,
        workspace: Workspace,
        read_plan: ReadPlan,
        task: Task,
    ) -> Any:
        """Bind the persistent task allowance across planning and coding nodes."""
        from Sprout.llm.task_budget import task_model_budget

        domain = (self._project_analysis_scope(task, workspace, node)
                  if self._project_analysis_scope is not None else nullcontext())
        with domain as scope:
            if scope is not None:
                from Sprout.task.paths import bound_scope_path, scope_path

                root = scope_path(str(workspace.root))
                paths = []
                for resource in read_plan.resources:
                    target = scope_path(bound_scope_path(resource.path, str(workspace.root)))
                    if target is None or not root.contains(target):
                        raise PermissionError("node_read_plan_unavailable")
                    paths.append(target.text()[len(root.text().rstrip("/")) + 1:])
                scope.known_paths = tuple(paths)
            with task_model_budget(self._metadata, task.id, node.id, task.budget.max_model_calls):
                result = await self._execute_node(node, workspace, read_plan, task)
                if scope is not None:
                    scope.runtime._remember_node_product(scope, result)
                return result

    async def _execute_node(self, node, workspace, read_plan, task):
        """Run one node and return its persisted output."""
        if node.type is NodeType.READ:
            return await self._read(workspace, read_plan, task)
        if node.type is NodeType.PLAN:
            return await self._plan(node, workspace, task)
        if node.type is NodeType.TEST_DESIGN:
            return await self._test_design(node, workspace, task)
        if node.type is NodeType.SUBTASK:
            return await self._subtask(node, workspace, task)
        if node.type is NodeType.AGENT:
            from Sprout.tools.context_keys import _current_project_node

            scope = _current_project_node()
            if scope is not None and node.id in scope.runtime._online_analysis_requests:
                return await self._online_project_analysis(node, workspace, task, scope)
            return await self._agent(node, workspace, task)
        if node.type is NodeType.SANDBOX:
            return await self._sandbox(workspace)
        if node.type is NodeType.EVALUATION:
            return await self._evaluation(node, workspace, task)
        if node.type is NodeType.APPROVAL:
            return await self._approval(workspace, task)
        if node.type is NodeType.APPLY:
            return await self._apply(workspace, task)
        return None

    async def pin_snapshot(
        self,
        recorder: JsonlTrajectoryRecorder,
        artifact_snapshot: ArtifactSnapshot | None,
        task_id: str,
    ) -> Skill | None:
        """Register the pinned artifact version so the run stays reproducible."""
        if artifact_snapshot is None:
            return None
        artifact = await self._metadata.get_artifact(artifact_snapshot.artifact_id)
        if artifact is None or artifact.version != artifact_snapshot.artifact_version:
            return None
        if (
            artifact_snapshot.content_hash is not None
            and sha256(artifact.content.encode()).hexdigest() != artifact_snapshot.content_hash
        ):
            raise ValueError("Pinned artifact content does not match snapshot")
        await recorder.record(
            TrajectoryEvent(
                name="artifact.pinned",
                task_id=task_id,
                payload={
                    "artifact_id": artifact.id,
                    "artifact_version": artifact.version,
                },
            )
        )
        if artifact.kind is not ArtifactKind.SKILL:
            return None
        existing = self._skills.list().get(artifact.name)
        if (existing is not None and existing.version == artifact.version
                and existing.instructions == artifact.content):
            # Reuse the exact loaded skill, including trust, tool requirements
            # and activation state. No temporary registration needs cleanup.
            return None
        pinned = Skill(
            name=artifact.name,
            version=artifact.version,
            instructions=artifact.content,
            required_tools=(),
            enabled=True,
        )
        self._skills.register(pinned)
        return pinned

    # -- node branches -----------------------------------------------------
    async def _check_model_publication(self, node, workspace, task):
        """Old persisted outputs have no resource/subject receipt: refuse reuse.

        This does not bless an old ALLOW or reconstruct a view from stored
        text. Only original task input with no upstream artifact dependencies
        can pass until a separately reviewed provenance contract exists.
        """
        from Sprout.tools.context_keys import _current_project_node

        scope = _current_project_node()
        if (scope is None or scope.node.id != node.id
                or scope.runtime._metadata_or_raise() is not self._metadata):
            raise PermissionError("node_publication_subject_unavailable")
        current, authoritative_workspace = await scope.runtime._project_analysis_subject(scope)
        persisted = await self._metadata.get_execution_node(node.id)
        if (current != task or persisted != node
                or (authoritative_workspace.id, authoritative_workspace.root,
                    authoritative_workspace.kind, authoritative_workspace.revision)
                != (workspace.id, workspace.root, workspace.kind, workspace.revision)
                or current.delegation_scope.is_expired()):
            raise PermissionError("node_publication_authority_changed")
        nodes = await self._metadata.list_execution_nodes(current.id)
        admitted = await scope.runtime._admit_node_products(scope, nodes)
        if (not admitted and (node.dependencies or node.result_ref is not None
                or any(item.task_id != current.id or item.result_ref is not None
                       or item.metadata.get("output") is not None for item in nodes)
                or set(node.metadata) - {"execution_guard", "read_only", "tool_names"})):
            raise PermissionError("node_artifact_provenance_unavailable")
        # Registry identity, not a flag carried in the model's metadata.
        scope.runtime._node_publications[node.id] = scope

    async def _publish_node_model(self, node, workspace, task, agent, message, context):
        # Recheck after all asynchronous preparation, before handing ANY text
        # to an agent/planner. No silent empty replacement on a denied input.
        await self._check_model_publication(node, workspace, task)
        from Sprout.tools.context_keys import _current_project_node

        scope = _current_project_node()
        if "db.read" in scope.task.delegation_scope.allowed_actions:
            tools = dict(context.tools)
            database = scope.runtime.tools.get("database_query")
            tools[database.spec.name] = database
            context = replace(context, tools=tools)
        if scope.known_paths:
            tools = dict(context.tools)
            for tool in (await scope.runtime._issue_project_analysis_tool(paths=scope.known_paths),
                         await scope.runtime._issue_project_query_tool(paths=scope.known_paths)):
                tools[tool.spec.name] = tool
            context = replace(context, tools=tools)
        for tool in context.tools.values():
            scope.runtime._node_tool_scopes[tool] = scope
        return await agent.run(message, context)

    async def _read(
        self, workspace: Workspace, read_plan: ReadPlan, task: Task
    ) -> list[dict]:
        from Sprout.tools.context_keys import _current_project_node

        scope = _current_project_node()
        if scope is not None:
            if not scope.known_paths:
                scope.read_captured = True
                return []
            return await scope.runtime._read_node_selection(scope.known_paths)
        # The task's delegation scope travels with every read (AUTHZ §2.2); a
        # channel that granted read-only access must not get more than that.
        results = await self._read_broker.read(
            workspace, read_plan, scope=task.delegation_scope
        )
        return [
            {
                "path": item.resource.path,
                "decision": item.decision.value,
                "content": item.content,
                "error": item.error,
            }
            for item in results
        ]

    async def _online_project_analysis(self, node, workspace, task, scope):
        """Only a Runtime-owned admission supplies this node's initial tool call."""
        await self._check_model_publication(node, workspace, task)
        admission = scope.runtime._online_analysis_requests[node.id]
        paths = admission["paths"]
        tool = await scope.runtime._issue_project_analysis_tool(
            paths=paths or None, discover=not paths,
        )
        tools = {tool.spec.name: tool}
        query = await scope.runtime._issue_project_query_tool(
            paths=paths or None, discover=not paths)
        tools[query.spec.name] = query
        context = self._contexts.build_node_context(
            user_id=task.actor.user_id, node_id=node.id, tools=tools,
            metadata={"task_id": task.id},
        )
        # This private host instruction is not recovered from JSON or metadata.
        context = replace(context, metadata={**context.metadata, "resume_tool_calls": [{
            "id": "host-project-analysis", "name": tool.spec.name,
            "arguments": {"path": admission["target"],
                          "instruction": admission["message"].content},
        }]})
        message = Message(content=admission["message"].content, channel="task",
                          user_id=task.actor.user_id)
        result = await self._publish_node_model(
            node, workspace, task, self._router.route(message), message, context,
        )
        return {"content": result.content, "analysis_ok": admission["analysis_result"] is not None}

    async def _agent(
        self, node: ExecutionNode, workspace: Workspace, task: Task
    ) -> dict:
        await self._check_model_publication(node, workspace, task)
        if node.metadata.get("read_only"):
            result = await self._plan(
                node, workspace, task,
            )
            return {"content": result["plan"], "read_only": True}
        nodes = await self._metadata.list_execution_nodes(task.id)
        sandbox_tools: list = []
        sandbox_ref = self._sandbox_ref_from(self._output_for(nodes, NodeType.SANDBOX))
        if sandbox_ref is not None:
            available_tools = {
                "sandbox_write_file": SandboxWriteTool(
                    sandbox_ref,
                    self._file_broker,
                    scope=task.delegation_scope,
                    task_id=task.id,
                ),
                "sandbox_read_file": SandboxReadTool(
                    sandbox_ref,
                    self._file_broker,
                    scope=task.delegation_scope,
                    task_id=task.id,
                ),
                "sandbox_edit_file": SandboxEditTool(
                    sandbox_ref,
                    self._file_broker,
                    scope=task.delegation_scope,
                    task_id=task.id,
                ),
                "sandbox_apply_patch": SandboxApplyPatchTool(
                    sandbox_ref,
                    self._file_broker,
                    scope=task.delegation_scope,
                    task_id=task.id,
                    source=task.source, requested_by=task.actor.user_id,
                ),
                "sandbox_list_files": SandboxListTool(sandbox_ref),
                "sandbox_search": SandboxSearchTool(sandbox_ref),
                "sandbox_git_inspect": SandboxGitTool(
                    sandbox_ref, allow_host_git=not self._isolated_coding_required),
            }
            tool_names = set(node.metadata.get("tool_names") or ())
            sandbox_tools = (
                [
                    tool
                    for name, tool in available_tools.items()
                    if name in tool_names
                ]
                if tool_names
                else list(available_tools.values())
            )
        resources = self._resources(self._output_for(nodes, NodeType.READ), workspace)
        sandbox_tools, resources = self._model_access(task, sandbox_ref, sandbox_tools, resources)
        if (self._isolated_coding_required and sandbox_ref is not None
                and callable(getattr(getattr(self, "_coding_commands", None), "execute", None))):
            allowed = set(node.metadata.get("tool_names") or ())
            if not allowed or "isolated_coding_command" in allowed:
                sandbox_tools.append(IsolatedCodingCommandTool(
                    self._coding_commands, task=task, node=node, sandbox=sandbox_ref))
        feedback = self._previous_evaluation_feedback(node, nodes)
        node_skills = {}
        allowed = set(node.metadata.get("tool_names") or ())
        if (self._isolated_coding_required and sandbox_ref is not None
                and (not allowed or "skill_view" in allowed)):
            registry = getattr(self, "_skills", None)
            node_skills = {name: replace(skill, origin="")
                for name, skill in (registry.list() if registry is not None else {}).items()
                if skill.enabled and skill.trust == "trusted"}
            if node_skills:
                # Node-local frozen bodies, not a global tool or host file reader.
                sandbox_tools.append(SkillViewTool(node_skills))
        plan_output = self._output_for(nodes, NodeType.PLAN)
        plan = (
            str(plan_output.get("plan", ""))
            if isinstance(plan_output, dict)
            else ""
        )
        findings = self._subtask_findings(nodes)
        design = self._output_for(nodes, NodeType.TEST_DESIGN)
        if isinstance(design, dict):
            if (not self._isolated_coding_required and self._local_acceptance
                    and local_evidence_applicability(design) != "match"):
                return self._acceptance_wait("local_executor_unknown_or_mismatched")
            visible_spec = (public_acceptance(design.get("acceptance_spec"))
                            if self._isolated_coding_required else design.get("acceptance_spec"))
            plan += (
                "\nFrozen requirement tests (do not modify these tests):\n"
                + json.dumps(visible_spec, ensure_ascii=False)
                + ("\nHost-owned isolated JSON checks; expected outputs are withheld."
                   if self._isolated_coding_required
                   else "\nTest file: " + str(design.get("test_path")))
            )
        if self._router is None:
            raise RuntimeError("No agent router configured")
        message = Message(
            content=self._agent_prompt(
                task, workspace, resources, feedback, plan, findings
            ),
            channel="task",
            user_id=task.actor.user_id,
            metadata={
                "task_id": task.id,
                "node_id": node.id,
                "resource_paths": [item["path"] for item in resources],
                "response_language": str(
                    task.metadata.get("response_language")
                    or task.metadata.get("cli_language")
                    or detect_language(task.instruction)
                ),
            },
        )
        # Node context: only this node's resources and tools, no inherited history.
        context = self._contexts.build_node_context(
            user_id=task.actor.user_id,
            node_id=node.id,
            tools={tool.spec.name: tool for tool in sandbox_tools},
            skills=node_skills,
            files=tuple(item["path"] for item in resources),
            metadata={
                "task_id": task.id,
                "resources": tuple(resources),
                "resume_tool_calls": (node.metadata.get("output") or {}).get(
                    "pending_tool_calls", (),
                ),
                "task_budget": {
                    "max_model_calls": task.budget.max_model_calls,
                    "max_tool_calls": task.budget.max_tool_calls,
                    "max_tokens": task.budget.max_tokens,
                },
            },
        )
        # No global register/unregister: the node's tools travel in
        # ``context.tools`` and the executor dispatches from there, so two
        # nodes running at once cannot unregister each other's tools.
        agent = self._router.route(message)
        result = await self._publish_node_model(node, workspace, task, agent, message, context)
        return {
            "content": result.content[:_NODE_OUTPUT_LIMIT],
            "node_id": node.id,
            "resources": [item["path"] for item in resources],
            "waiting": bool(result.metadata.get("approval_required")),
            "pending_approvals": list(result.metadata.get("approval_ids") or ()),
            "pending_tool_calls": list(result.metadata.get("pending_tool_calls") or ()),
        }

    async def _plan(
        self, node: ExecutionNode, workspace: Workspace, task: Task,
        *, prompt: str | None = None,
    ) -> dict:
        """Run a read-only planning pass before the first edit round."""
        await self._check_model_publication(node, workspace, task)
        if prompt is not None:
            raise PermissionError("node_artifact_provenance_unavailable")
        nodes = await self._metadata.list_execution_nodes(task.id)
        sandbox_ref = self._sandbox_ref_from(self._output_for(nodes, NodeType.SANDBOX))
        plan_tools: list = []
        if sandbox_ref is not None:
            plan_tools = [
                SandboxReadTool(
                    sandbox_ref,
                    self._file_broker,
                    scope=task.delegation_scope,
                    task_id=task.id,
                ),
                SandboxListTool(sandbox_ref),
                SandboxSearchTool(sandbox_ref),
                SandboxGitTool(sandbox_ref, allow_host_git=not self._isolated_coding_required),
            ]
        resources = self._resources(self._output_for(nodes, NodeType.READ), workspace)
        plan_tools, resources = self._model_access(task, sandbox_ref, plan_tools, resources)
        if self._router is None:
            raise RuntimeError("No agent router configured")
        message = Message(
            content=prompt or self._plan_prompt(task, workspace, resources),
            channel="task",
            user_id=task.actor.user_id,
            metadata={
                "task_id": task.id,
                "node_id": node.id,
                "resource_paths": [item["path"] for item in resources],
            },
        )
        context = self._contexts.build_node_context(
            user_id=task.actor.user_id,
            node_id=node.id,
            tools={tool.spec.name: tool for tool in plan_tools},
            files=tuple(item["path"] for item in resources),
            metadata={
                "task_id": task.id,
                "resources": tuple(resources),
            },
        )
        agent = self._router.route(message)
        result = await self._publish_node_model(node, workspace, task, agent, message, context)
        return {
            "plan": result.content[:_NODE_OUTPUT_LIMIT],
            "node_id": node.id,
        }

    async def _test_design(
        self, node: ExecutionNode, workspace: Workspace, task: Task,
    ) -> dict:
        """Freeze concrete input/output tests and record pre-change results."""
        nodes = await self._metadata.list_execution_nodes(task.id)
        sandbox = self._sandbox_ref_from(self._output_for(nodes, NodeType.SANDBOX))
        if sandbox is None:
            raise RuntimeError("Acceptance tests require an isolated Git worktree")
        previous = node.metadata.get("output") or {}
        if self._isolated_coding_required:
            payload = task.metadata.get("acceptance_spec")
            if payload is None:
                return {"waiting": True, "waiting_kind": "configuration",
                        "reason": "frozen_source_acceptance_required"}
            spec = AcceptanceSpec.from_payload(payload)
            fingerprint = sha256(json.dumps(spec.to_payload(), sort_keys=True).encode()).hexdigest()
            if (previous.get("acceptance_fingerprint")
                    and previous["acceptance_fingerprint"] != fingerprint):
                raise RuntimeError("Frozen source acceptance changed")
            if self._coding_session_factory is None:
                return {"waiting": True, "waiting_kind": "configuration",
                        "reason": "isolated_session_not_assembled",
                        "acceptance_spec": spec.to_payload(), "acceptance_fingerprint": fingerprint}
            session = self._coding_session_factory(task=task, node=node, sandbox=sandbox)
            measured = await session.verify(acceptance_spec=spec.to_payload(),
                                            regression_spec=None, phase="baseline")
            return {**measured, "isolated_design": True, "acceptance_spec": spec.to_payload(),
                    "acceptance_fingerprint": fingerprint, "baseline": measured.get("results", []),
                    "baseline_failed": measured.get("status") == "failed"}
        if task.metadata.get("host_acceptance_v1") is not None:
            return await self._host_test_design(node, workspace, task, sandbox)
        payload = previous.get("acceptance_spec")
        binding = None
        if self._local_acceptance:
            if (previous.get("acceptance_spec") is not None
                    and local_executor_applicability(previous.get("local_executor")) != "match"):
                return {**previous, **self._acceptance_wait("local_executor_unknown_or_mismatched")}
            try:
                binding = local_executor_binding()
            except (OSError, ValueError, ImportError):
                return self._acceptance_wait("local_executor_unavailable")
        if payload is None:
            resources = self._resources(self._output_for(nodes, NodeType.READ), workspace)
            prompt = acceptance_prompt(task.instruction) + "\nProject evidence:\n" + json.dumps(
                resources, ensure_ascii=False,
            )
            planned = await self._plan(node, workspace, task, prompt=prompt)
            payload = ModelStrategyPlanner._parse_json(planned["plan"])
        spec = AcceptanceSpec.from_payload(payload)
        path = acceptance_test_path(task.id)
        source = render_acceptance_tests(spec)
        written = await self._file_broker.write_text(
            sandbox, path, source, task_id=task.id, scope=task.delegation_scope,
        )
        if not written.wrote:
            raise RuntimeError(f"Cannot write acceptance tests: {written.reason}")
        demo_path = acceptance_demo_path(task.id)
        demo_source = render_acceptance_demo(spec)
        demo_written = await self._file_broker.write_text(
            sandbox, demo_path, demo_source, task_id=task.id, scope=task.delegation_scope,
        )
        if not demo_written.wrote:
            raise RuntimeError(f"Cannot write minimal demo: {demo_written.reason}")
        baseline = []
        pending = []
        for index, (case, command) in enumerate(zip(
            spec.cases, acceptance_commands(spec, path, python="python"), strict=True
        )):
            if binding is not None:
                outcome = await self._local_acceptance_run(
                    workspace, task, sandbox.root, spec, path, case.id, binding)
            else:
                outcome = await self._run_verification_command(
                    workspace, command, sandbox.root, task, 1,
                    node=node, command_slot=f"baseline-{index}",
                )
            if outcome.needs_approval:
                pending.append(outcome.approval_id)
            elif not outcome.allowed:
                raise RuntimeError(f"Acceptance baseline was denied: {outcome.error}")
            baseline.append({
                "case_id": case.id, "exit_code": outcome.exit_code,
                "executed": outcome.allowed,
                "output": _clamp_test_output(outcome.stdout, outcome.stderr, outcome.error),
                **({"category": local_acceptance_evidence(outcome, binding)}
                   if binding is not None else {}),
            })
        output = {
            "acceptance_spec": spec.to_payload(), "test_path": path,
            "test_hash": sha256(source.encode()).hexdigest(), "baseline": baseline,
            "demo_path": demo_path, "demo_hash": sha256(demo_source.encode()).hexdigest(),
            "baseline_failed": any(item["exit_code"] == 1 for item in baseline),
            "waiting": bool(pending), "pending_approvals": pending,
        }
        if binding is not None:
            output["local_executor"] = binding
            red = {"assertion_failed", "requirement_not_implemented",
                   "target_module_not_implemented", "target_exception"}
            output["baseline_failed"] = any(item["category"] in red for item in baseline)
            output["local_baseline_valid"] = (not pending and all(
                item["category"] in red | {"passed"} for item in baseline))
            if not pending and not output["local_baseline_valid"]:
                output.update(self._acceptance_wait("local_baseline_invalid"))
        return output

    async def _host_test_design(self, node, workspace, task, sandbox):
        from Sprout.execution.host_acceptance import check_binding
        from Sprout.tools.context_keys import _current_project_node

        # This admits READ/SANDBOX/PLAN with fresh authority and real source
        # receipts. A host specification cannot excuse an untracked model effect.
        await self._check_model_publication(node, workspace, task)
        frozen = task.metadata["host_acceptance_v1"]
        spec = check_binding(task, workspace, frozen)
        scope = _current_project_node()
        receipts = {entry["path"]: entry for entry in scope.resource_receipts}
        expected = {frozen["test_path"]: render_acceptance_tests(spec),
                    frozen["demo_path"]: render_acceptance_demo(spec)}
        for path, text in expected.items():
            pinned = frozen["resources"][path]
            latest = receipts.get(path)
            if latest is None or any(latest[k] != pinned[k] for k in ("digest", "identity")):
                raise PermissionError("host_acceptance_source_changed")
            read = await self._file_broker.read_text(
                sandbox, path, task_id=task.id, scope=task.delegation_scope, actor=task.actor)
            if not read.wrote or read.content.replace("\r\n", "\n") != text:
                raise PermissionError("host_acceptance_worktree_changed")
        executor = local_executor_binding()
        baseline = []
        for case in spec.cases:
            outcome = await self._local_acceptance_run(
                workspace, task, sandbox.root, spec, frozen["test_path"], case.id, executor)
            if outcome.allowed and outcome.exit_code is None and outcome.started is not False:
                from Sprout.tools.result import ToolExecutionUnconfirmed

                raise ToolExecutionUnconfirmed("Frozen pytest execution result unconfirmed")
            if not outcome.allowed:
                return {**self._acceptance_wait(outcome.error or "acceptance_execution_denied"),
                        "waiting_kind": "approval" if outcome.approval_id else "resource",
                        "pending_approvals": [outcome.approval_id] if outcome.approval_id else [],
                        "baseline": baseline, "binding_sha256": frozen["sha256"]}
            baseline.append({"case_id": case.id, "exit_code": outcome.exit_code,
                "executed": True, "output": _clamp_test_output(
                    outcome.stdout, outcome.stderr, outcome.error),
                "category": local_acceptance_evidence(outcome, executor)})
        valid = all(r["category"] in {
            "passed", "assertion_failed", "requirement_not_implemented",
            "target_module_not_implemented", "target_exception"} for r in baseline)
        return {"acceptance_spec": spec.to_payload(), "test_path": frozen["test_path"],
                "test_hash": sha256(render_acceptance_tests(spec).encode()).hexdigest(),
                "demo_path": frozen["demo_path"],
                "demo_hash": sha256(render_acceptance_demo(spec).encode()).hexdigest(),
                "binding_sha256": frozen["sha256"], "local_executor": executor,
                "baseline": baseline, "baseline_failed": any(r["exit_code"] == 1 for r in baseline),
                "local_baseline_valid": valid, "waiting": False,
                **(self._acceptance_wait("local_baseline_invalid") if not valid else {})}

    @staticmethod
    def _acceptance_wait(reason):
        return {"waiting": True, "waiting_kind": "configuration", "reason": reason,
                "status": "unverified", "verified": False}

    async def _local_acceptance_run(self, workspace, task, cwd, spec, path, case_id, binding):
        if self._isolated_coding_required or not self._local_acceptance:
            raise RuntimeError("Local acceptance unavailable for this backend")
        contract = None
        if task.metadata.get("host_acceptance_v1"):
            from Sprout.execution.models import ProcessResult
            from Sprout.tools.context_keys import _current_project_node

            scope = _current_project_node()
            try:
                if scope is None:
                    raise PermissionError("Frozen acceptance node unavailable")
                contract = await scope.runtime._issue_frozen_execution(
                    cwd, spec, path, case_id, binding)
            except (OSError, ValueError) as exc:
                return ProcessResult(command=(), allowed=False, started=False, error=str(exc))
        for attempt in range(2):
            execution = (scope.runtime._frozen_execution_record(contract, self._process_broker)
                         if contract is not None else None)
            outcome = await self._process_broker.run_local_acceptance(
                workspace, spec, path, case_id, binding, cwd=cwd, task_id=task.id,
                scope=task.delegation_scope, command_origin="manifest",
                source=str(task.actor.source or "interactive"),
                requested_by=task.actor.user_id or "system",
                actor=task.actor,
                resource_scope=task.workspace_id,
                **({"execution_contract": contract, "session_id": scope.session_id}
                   if contract is not None else {}),
            )
            if execution is None or not outcome.approval_id or attempt:
                break
            try:
                await self._process_broker._wait_frozen_approval(execution, outcome.approval_id)
                contract = await scope.runtime._issue_frozen_execution(
                    cwd, spec, path, case_id, binding)
                fresh = scope.runtime._frozen_execution_record(contract, self._process_broker)
                fresh["approved_record"] = outcome.approval_id
            except (OSError, ValueError) as exc:
                return replace(outcome, error=str(exc), approval_id=None)
        if (contract is not None and outcome.allowed and outcome.exit_code is None
                and outcome.started is not False):
            from Sprout.tools.result import ToolExecutionUnconfirmed

            raise ToolExecutionUnconfirmed("Frozen pytest execution result unconfirmed")
        return outcome

    async def _subtask(
        self, node: ExecutionNode, workspace: Workspace, task: Task
    ) -> dict:
        """Investigate one plan step and report evidence; never edits.

        Read-only by construction: the tool set is the planning set, so a
        subtask cannot write even if the model asks it to. Its output is
        evidence for the edit round, not a change.
        """
        await self._check_model_publication(node, workspace, task)
        metadata = node.metadata
        description = str(metadata.get("description") or "").strip()
        if not description:
            return {"status": "skipped", "reason": "empty step description"}

        nodes = await self._metadata.list_execution_nodes(task.id)
        sandbox_ref = self._sandbox_ref_from(self._output_for(nodes, NodeType.SANDBOX))
        tools: list = []
        if sandbox_ref is not None:
            tools = [
                SandboxReadTool(
                    sandbox_ref,
                    self._file_broker,
                    scope=task.delegation_scope,
                    task_id=task.id,
                ),
                SandboxListTool(sandbox_ref),
                SandboxSearchTool(sandbox_ref),
                SandboxGitTool(sandbox_ref, allow_host_git=not self._isolated_coding_required),
            ]

        # Prefer the step's own target files; fall back to the task's read plan
        # so a step without paths still has something to reason about.
        target_paths = [
            str(path) for path in metadata.get("target_paths") or () if str(path).strip()
        ]
        all_resources = self._resources(
            self._output_for(nodes, NodeType.READ), workspace
        )
        if target_paths:
            wanted = set(target_paths)
            resources = tuple(
                item for item in all_resources if item["path"] in wanted
            ) or all_resources
        else:
            resources = all_resources

        tools, resources = self._model_access(task, sandbox_ref, tools, resources)
        if self._router is None:
            raise RuntimeError("No agent router configured")
        message = Message(
            content=self._subtask_prompt(description, metadata, resources),
            channel="task",
            user_id=task.actor.user_id,
            metadata={
                "task_id": task.id,
                "node_id": node.id,
                "resource_paths": [item["path"] for item in resources],
            },
        )
        context = self._contexts.build_node_context(
            user_id=task.actor.user_id,
            node_id=node.id,
            tools={tool.spec.name: tool for tool in tools},
            files=tuple(item["path"] for item in resources),
            metadata={
                "task_id": task.id,
                "resources": tuple(resources),
            },
        )
        agent = self._router.route(message)
        result = await self._publish_node_model(node, workspace, task, agent, message, context)
        return {
            "node_id": node.id,
            "index": metadata.get("index"),
            "kind": metadata.get("kind", "other"),
            "description": description,
            "findings": result.content[:_NODE_OUTPUT_LIMIT],
        }

    def _model_access(self, task, sandbox, tools, resources):
        if not self._isolated_coding_required:
            if task.execution_mode == "code":
                # These legacy tools access the host without per-resource Broker checks.
                tools = [tool for tool in tools if not isinstance(
                    tool, (SandboxListTool, SandboxSearchTool, SandboxGitTool))]
            return tools, resources
        if sandbox is None:
            raise ValueError("Isolated model tools require a managed sandbox")
        guard = IsolatedModelPathGuard(sandbox, task)
        return ([IsolatedModelTool(tool, guard) for tool in tools],
                tuple(item for item in resources if guard.allows(item["path"])))

    async def _sandbox(self, workspace: Workspace) -> dict:
        if workspace.kind is not WorkspaceKind.GIT_REPOSITORY:
            return {
                "status": "not_applicable",
                "reason": "workspace is not a Git repository",
            }
        provider = (IsolatedGitWorktreeSandbox if self._isolated_coding_required
                    else GitWorktreeSandbox)
        ref = await provider(workspace).create()
        if not self._isolated_coding_required:
            from Sprout.tools.context_keys import _current_project_node

            scope = _current_project_node()
            if scope is not None and scope.task.metadata.get("host_acceptance_v1"):
                await scope.runtime._register_frozen_sandbox(ref)
        return {
            "id": ref.id,
            "kind": ref.kind,
            "root": str(ref.root),
            "worktree_ref": ref.worktree_ref,
        }

    async def _evaluation(
        self, node: ExecutionNode, workspace: Workspace, task: Task
    ) -> dict:
        """Run the manifest's test and build commands against the sandbox.

        Verification used to run with ``cwd=workspace.root`` while every change
        the task made lived in the sandbox worktree, so a green result proved
        nothing about the diff waiting for approval. It now runs where the agent
        wrote. ``build_commands`` was carried in node metadata and never read.
        """
        from Sprout.tools.context_keys import _current_project_node

        scope = _current_project_node()
        if scope is not None and task.id in scope.runtime._verification_hosts:
            return await scope.runtime._evaluate_host_verification(node)
        nodes = await self._metadata.list_execution_nodes(task.id)
        sandbox_ref = self._sandbox_ref_from(self._output_for(nodes, NodeType.SANDBOX))
        cwd = sandbox_ref.root if sandbox_ref is not None else workspace.root
        if self._isolated_coding_required:
            if self._coding_session_factory is None or sandbox_ref is None:
                return {"waiting": True, "waiting_kind": "configuration",
                        "reason": "isolated_session_not_assembled", "status": "unverified"}
            session = self._coding_session_factory(task=task, node=node, sandbox=sandbox_ref)
            has_auxiliary = any(node.metadata.get(key) for key in
                   ("test_commands", "build_commands", "verification_commands"))
            if has_auxiliary and not callable(getattr(session, "verify_auxiliary", None)):
                return {"waiting": True, "waiting_kind": "configuration",
                        "reason": "isolated_auxiliary_checks_adapter_required",
                        "status": "unverified"}
            auxiliary_commands(node.metadata)  # Reject malformed empty declarations too.
            design = self._output_for(nodes, NodeType.TEST_DESIGN)
            if not isinstance(design, dict):
                raise RuntimeError("Frozen isolated acceptance design missing")
            if design.get("isolated_design") is not True:
                return {"waiting": True, "waiting_kind": "configuration",
                        "reason": "isolated_design_protocol_required", "status": "unverified"}
            pinned = AcceptanceSpec.from_payload(task.metadata.get("acceptance_spec")).to_payload()
            fingerprint = sha256(json.dumps(pinned, sort_keys=True).encode()).hexdigest()
            if (design.get("acceptance_spec") != pinned
                    or design.get("acceptance_fingerprint") != fingerprint):
                raise RuntimeError("Frozen isolated acceptance design changed")
            regression_spec = getattr(session, "regression_spec", None)
            auxiliary = None
            if callable(getattr(session, "verify_auxiliary", None)):
                if regression_spec is None:
                    return {"waiting": True, "waiting_kind": "configuration",
                            "reason": "frozen_regression_spec_required", "verified": False}
                auxiliary = await session.verify_auxiliary(node.metadata)
                if auxiliary.get("waiting"):
                    return auxiliary
            measured = await session.verify(acceptance_spec=design["acceptance_spec"],
                                            regression_spec=regression_spec, phase="evaluation")
            if not measured.get("waiting"):
                if auxiliary is not None:
                    measured = dict(measured)
                    measured["results"] = [*measured.get("results", []),
                                           *auxiliary.get("results", [])]
                    measured["test_results"] = [*measured.get("test_results", []),
                                                *auxiliary.get("test_results", [])]
                    measured["verified"] = (measured.get("verified") is True
                                            and auxiliary.get("verified") is True)
                    if auxiliary.get("status") != "passed":
                        measured["status"] = "failed"
                measured["verification_report"] = build_verification_report(
                    self._test_results_from(measured), measured).to_payload()
            return measured

        planned: list[tuple[str, tuple[str, ...]]] = [
            ("test", tuple(str(command).split()))
            for command in node.metadata.get("test_commands", ())
        ]
        planned.extend(
            ("build", tuple(str(command).split()))
            for command in node.metadata.get("build_commands", ())
        )
        # Requirement-specific acceptance commands from the strategy layer,
        # keyed by their criterion kind so the results read as "database/schema
        # failed" rather than "test #4 failed".
        for item in node.metadata.get("verification_commands", ()):
            if not isinstance(item, dict):
                continue
            command = str(item.get("command") or "").strip()
            if not command:
                continue
            planned.append((str(item.get("kind") or "acceptance"), tuple(command.split())))
        planned.extend(await self._static_check_commands(workspace, sandbox_ref))
        design = self._output_for(nodes, NodeType.TEST_DESIGN)
        acceptance_spec = None
        local_cases = {}
        if isinstance(design, dict):
            if self._local_acceptance and local_evidence_applicability(design) != "match":
                return self._acceptance_wait("local_executor_unknown_or_mismatched")
            acceptance_spec = AcceptanceSpec.from_payload(design.get("acceptance_spec"))
            test_path = str(design["test_path"])
            read = await self._file_broker.read_text(
                sandbox_ref, test_path, task_id=task.id, scope=task.delegation_scope,
            )
            if not read.wrote or sha256(read.content.encode()).hexdigest() != design["test_hash"]:
                raise RuntimeError("Frozen acceptance tests were removed or modified")
            demo_path = str(design["demo_path"])
            demo = await self._file_broker.read_text(
                sandbox_ref, demo_path, task_id=task.id, scope=task.delegation_scope,
            )
            if not demo.wrote or sha256(demo.content.encode()).hexdigest() != design["demo_hash"]:
                raise RuntimeError("Frozen minimal demo was removed or modified")
            planned.extend(
                (f"acceptance:{case.id}", command)
                for case, command in zip(
                    acceptance_spec.cases,
                    acceptance_commands(acceptance_spec, test_path, python="python"),
                    strict=True,
                )
            )
            if self._local_acceptance:
                local_cases = {f"acceptance:{case.id}": case.id for case in acceptance_spec.cases}
            planned.append(("poc", ("python", demo_path)))
        elif task.metadata.get("require_acceptance_tests"):
            raise RuntimeError("Requirement acceptance tests are missing")
        if not planned:
            planned = self._fallback_verification(workspace)
        attempts = max(1, int(node.metadata.get("verification_attempts", 1)))

        records: list[dict] = []
        test_results: list[TestResult] = []
        failed: list[str] = []
        withheld: list[str] = []
        pending_approvals: list[str] = []
        for index, (kind, parts) in enumerate(planned):
            if not parts:
                continue
            if kind in local_cases:
                outcome = await self._local_acceptance_run(
                    workspace, task, cwd, acceptance_spec, test_path,
                    local_cases[kind], design["local_executor"])
                evidence = local_acceptance_evidence(outcome, design["local_executor"])
                command_outcome = (
                    "passed" if evidence == "passed" else
                    "approval_required" if outcome.needs_approval else "check_failed")
            else:
                outcome = await self._run_verification_command(
                    workspace, parts, cwd, task, attempts,
                    node=node, command_slot=f"evaluation-{index}",
                )
                command_outcome = classify_verification_command(outcome)
            passed = command_outcome == "passed"
            name = f"{kind}: {' '.join(parts)}"
            test_results.append(
                TestResult(
                    name=name,
                    passed=passed,
                    output=_clamp_test_output(
                        outcome.stdout, outcome.stderr, outcome.error
                    ),
                    duration_ms=outcome.duration_ms,
                    # Distinguishes "ran and failed" from "never ran", which the
                    # apply gate needs to decide about.
                    executed=outcome.allowed,
                )
            )
            records.append(
                {
                    "kind": kind,
                    "name": name,
                    "outcome": command_outcome,
                    "cwd": str(cwd),
                    "command": list(outcome.command if kind in local_cases else parts),
                    "exit_code": outcome.exit_code,
                    "allowed": outcome.allowed,
                    "allowlisted": outcome.allowlisted,
                    "passed": passed,
                    "attempts": attempts,
                    "error": outcome.error,
                    # Set when the command is parked on a human grant, so the
                    # node can wait for it instead of concluding.
                    "approval_id": outcome.approval_id,
                }
            )
            if outcome.needs_approval:
                pending_approvals.append(outcome.approval_id)
            if not outcome.allowed:
                withheld.append(name)
            elif not passed:
                failed.append(name)

        status = verification_status(
            executed=len(test_results),
            failed=len(failed),
            withheld=len(withheld),
        )
        output = {
            "test_commands": [
                " ".join(command) for kind, command in planned if kind == "test"
            ],
            "build_commands": [
                " ".join(command) for kind, command in planned if kind == "build"
            ],
            "check_commands": [
                " ".join(command) for kind, command in planned if kind == "check"
            ],
            "cwd": str(cwd),
            "sandbox_root": str(sandbox_ref.root) if sandbox_ref is not None else None,
            "results": records,
            "test_results": [_test_result_payload(item) for item in test_results],
            "status": status,
            "acceptance_spec": acceptance_spec.to_payload() if acceptance_spec else None,
            "baseline": design.get("baseline") if isinstance(design, dict) else None,
            "demo_path": design.get("demo_path") if isinstance(design, dict) else None,
            "failed_commands": failed,
            # Carried separately so an approver can tell "this failed" from
            # "nobody checked": the two need different decisions.
            "unverified_commands": withheld,
            # Commands parked on a grant. The node WAITS rather than
            # concluding, because "completed" is terminal and would make the
            # approval pointless: resume skips completed nodes, so the granted
            # command would never re-run and the change would land on a
            # verification result that predates the grant.
            "waiting": bool(pending_approvals),
            "pending_approvals": list(dict.fromkeys(pending_approvals)),
            **({"local_executor": design["local_executor"],
                "local_baseline_valid": design["local_baseline_valid"]} if local_cases else {}),
        }
        output["verification_report"] = build_verification_report(
            test_results, output,
        ).to_payload()
        return output

    async def _run_verification_command(
        self,
        workspace: Workspace,
        parts: tuple[str, ...],
        cwd: Path,
        task: Task,
        attempts: int,
        *, node: ExecutionNode | None = None, command_slot: str = "",
    ) -> ProcessResult:
        """Run one verification command, retrying transient failures."""
        if self._isolated_coding_required:
            if node is None or not command_slot:
                raise RuntimeError("Isolated command identity unavailable")
            if self._coding_dispatch is None:
                raise RuntimeError("Isolated coding backend not ready; no host fallback")
            return await self._coding_dispatch(
                task=task, node=node, command=parts, cwd=cwd, command_slot=command_slot)
        outcome = None
        for attempt in range(attempts):
            outcome = await self._process_broker.run(
                workspace,
                parts,
                cwd=cwd,
                task_id=task.id,
                scope=task.delegation_scope,
                command_origin="manifest",
                # Carried so a withheld command can be attributed to the task
                # and its source, which is what decides whether a human can be
                # asked at all.
                source=str(task.actor.source or "interactive"),
                requested_by=task.actor.user_id or "system",
                resource_scope=task.workspace_id,
            )
            if outcome.allowed and outcome.exit_code == 0:
                break
            if attempt + 1 < attempts and not outcome.allowed:
                break
        return outcome

    async def _static_check_commands(
        self,
        workspace: Workspace,
        sandbox_ref: SandboxRef | None,
    ) -> list[tuple[str, tuple[str, ...]]]:
        """Build changed-file scoped lint/typecheck commands when tools exist."""
        if sandbox_ref is None or workspace.kind is not WorkspaceKind.GIT_REPOSITORY:
            return []
        changed = await GitWorktreeSandbox(workspace).changed_files(sandbox_ref)
        manifest = workspace.manifest
        languages = set(manifest.detected_languages) if manifest is not None else set()
        commands: list[tuple[str, tuple[str, ...]]] = []

        python_files = tuple(path for path in changed
                             if path.endswith(".py") and (sandbox_ref.root / path).is_file())
        if python_files:
            commands.append(("check", ("python", "-m", "compileall", "-q", *python_files)))
            if shutil.which("ruff"):
                commands.append(("check", ("ruff", "check", *python_files)))

        if languages & {"typescript", "node"}:
            ts_files = tuple(
                path for path in changed if path.endswith((".ts", ".tsx"))
            )
            if ts_files and shutil.which("tsc") and (sandbox_ref.root / "tsconfig.json").exists():
                commands.append(("check", ("tsc", "--noEmit")))
        return commands

    @staticmethod
    def _fallback_verification(
        workspace: Workspace,
    ) -> list[tuple[str, tuple[str, ...]]]:
        """Offer a cheap static check when the manifest found no test/build."""
        manifest = workspace.manifest
        languages = set(manifest.detected_languages) if manifest is not None else set()
        if "python" in languages:
            return [("check", ("python", "-m", "compileall", "-q", "."))]
        return []

    async def _approval(self, workspace: Workspace, task: Task) -> dict:
        nodes = await self._metadata.list_execution_nodes(task.id)
        sandbox_ref = self._sandbox_ref_from(
            self._output_for(nodes, NodeType.SANDBOX)
        )
        existing = await self._metadata.list_change_proposals(task.id)
        if existing:
            # Approval is resumable: never mint a second proposal for the same
            # task when the graph is continued after a human decision.
            proposal = existing[0]
            return {
                "proposal_id": proposal.id,
                "status": proposal.status.value,
                "files_changed": list(proposal.files_changed),
                "waiting": proposal.status is not ChangeProposalStatus.APPROVED,
            }

        evaluation = self._output_for(nodes, NodeType.EVALUATION)
        test_results = self._test_results_from(evaluation)
        # Commands the policy withheld. Distinct from a failure: nobody looked.
        # They must be removed from ``failed`` before it is used for risk
        # grading and the apply gate, or an unverified change gets treated as a
        # known-bad one and blocks a merge that was never actually tested.
        unverified = (
            list(evaluation.get("unverified_commands") or [])
            if isinstance(evaluation, dict)
            else []
        )
        withheld = set(unverified)
        failed = [
            item.name
            for item in test_results
            if not item.passed and item.name not in withheld
        ]

        diffs = []
        files_changed: tuple[str, ...] = ()
        if sandbox_ref is not None:
            provider = (IsolatedGitWorktreeSandbox if self._isolated_coding_required
                        else GitWorktreeSandbox)
            sandbox = provider(workspace)
            diff = await sandbox.diff(sandbox_ref)
            if diff.diff_text:
                diffs.append(diff)
            files_changed = await sandbox.changed_files(sandbox_ref)

        if not files_changed and not diffs:
            # A turn that produced no file changes has nothing to apply, so it
            # must not park on a change proposal. Read-only and conversational
            # tasks finish here and return the agent's answer instead.
            return {
                "status": "no_changes",
                "files_changed": [],
                "test_status": (
                    evaluation.get("status") if isinstance(evaluation, dict) else None
                ),
                "failed_commands": failed,
                "unverified_commands": unverified,
            }

        proposal = self._change_builder.build(
            task_id=task.id,
            sandbox_ref=sandbox_ref,
            files_changed=files_changed,
            test_results=tuple(test_results),
            diffs=tuple(diffs),
            # A change whose verification failed is not a "medium risk" change:
            # the approver has to see that, and ``ChangeProposalService.apply``
            # refuses to land it without an explicit override.
            #
            # Unverified is deliberately *not* upgraded to high: the diff was
            # never judged, so calling it high would overstate what is known.
            # It rides in metadata instead, where the approver reads it.
            risk="high" if failed else "medium",
            rollback_plan="",
        )
        proposal = replace(
            proposal,
            rollback_plan=(
                f"Roll back the applied commit for change proposal {proposal.id} "
                f"with `sprout project rollback {proposal.id}`. "
                f"Files: {', '.join(files_changed) or '(none)'}."
            ),
            metadata={
                "summary": task.instruction,
                # An approver must be able to tell "this passed", "this failed"
                # and "nobody checked" apart — they are different decisions.
                "verification": {
                    # Every authored change needs requirement evidence, even
                    # when a legacy/direct task omitted the design marker.
                    "acceptance_required": True,
                    "status": (
                        evaluation.get("status")
                        if isinstance(evaluation, dict)
                        else None
                    ),
                    "failed_commands": failed,
                    "unverified_commands": unverified,
                    **({key: evaluation.get(key) for key in (
                        "acceptance_spec", "baseline", "demo_path", "results", "cwd",
                        "verification_report",
                    )} if isinstance(evaluation, dict) else {}),
                    **({"local_executor": evaluation["local_executor"]}
                       if isinstance(evaluation, dict) and "local_executor" in evaluation else {}),
                    **({"local_baseline_valid": evaluation["local_baseline_valid"]}
                       if isinstance(evaluation, dict)
                       and "local_baseline_valid" in evaluation else {}),
                },
            },
        )
        await self._metadata.save_change_proposal(proposal)
        # Park the graph until a human grants or rejects the proposal.
        return {
            "proposal_id": proposal.id,
            "status": proposal.status.value,
            "files_changed": list(files_changed),
            "test_status": (
                evaluation.get("status") if isinstance(evaluation, dict) else None
            ),
            "failed_commands": failed,
            "unverified_commands": unverified,
            "waiting": True,
        }

    async def _apply(self, workspace: Workspace, task: Task) -> dict:
        if self._isolated_coding_required:
            return {"waiting": True, "waiting_kind": "configuration",
                    "reason": "isolated_apply_adapter_required", "applied": False}
        proposals = await self._metadata.list_change_proposals(task.id)
        if not proposals:
            if task.metadata.get("require_acceptance_tests"):
                raise RuntimeError("Coding task produced no verified change proposal")
            return {"status": "no_change_proposal"}
        proposal = proposals[0]
        verification = proposal.metadata.get("verification")
        applicability = verification_evidence_applicability(
            verification, local_execution_available=self._local_acceptance)
        if applicability != "local_match":
            return {**self._acceptance_wait(
                "local_executor_" + applicability.removeprefix("local_")),
                "applied": False}
        if proposal.status is ChangeProposalStatus.APPLIED:
            if proposal.metadata.get("verification_override"):
                raise RuntimeError("Change was force-applied; the task is not verified")
            blocker = proposal_verification_blocker(proposal)
            if blocker:
                raise RuntimeError(blocker)
            return {"proposal_id": proposal.id, "applied": True, "already_applied": True}
        if proposal.status is not ChangeProposalStatus.APPROVED:
            return {
                "proposal_id": proposal.id,
                "status": proposal.status.value,
                "waiting": True,
            }
        blocker = proposal_verification_blocker(proposal)
        if blocker:
            raise RuntimeError(blocker)
        if self._changes is not None:
            result = await self._changes.apply(proposal.id)
        else:
            result = await self._apply_broker.apply(
                proposal, workspace, scope=task.delegation_scope
            )
        if not result.applied:
            # The graph must not report success for a change that never
            # landed. It used to: a refused apply returned a normal payload,
            # the node was marked COMPLETED, and the task finished as
            # ``completed`` while the workspace was untouched — so a caller
            # that trusted the status believed the code was in.
            #
            # Raising marks the node (and the task) FAILED with the reason,
            # which is both true and recoverable: FAILED -> RETRYING is a legal
            # transition, so landing it later with an override still works.
            raise RuntimeError(
                f"Apply did not land change proposal {result.proposal_id}: "
                f"{result.reason or 'no reason given'}"
            )
        return {
            "proposal_id": result.proposal_id,
            "applied": result.applied,
            "reason": result.reason,
        }

    # -- output helpers ----------------------------------------------------
    @staticmethod
    def _output_for(
        nodes: list[ExecutionNode], node_type: NodeType
    ) -> dict | list | None:
        """The most recent *real* output of ``node_type``.

        Placeholder outputs are skipped. The verify-loop marks the rounds it
        does not run as SKIPPED with ``{"skipped": True}``, and taking the last
        match blindly meant downstream readers got an empty placeholder instead
        of the evaluation that actually ran — the approval node was building
        change proposals from a skipped node, so the verification status never
        reached the approver.
        """
        for node in reversed(nodes):
            if node.type is not node_type:
                continue
            output = node.metadata.get("output")
            if not isinstance(output, dict | list):
                continue
            if isinstance(output, dict) and output.get("skipped"):
                continue
            return output
        return None

    @staticmethod
    def _subtask_findings(nodes: list[ExecutionNode]) -> tuple[dict[str, Any], ...]:
        """Every SUBTASK node's output, in step order.

        Unlike :meth:`_output_for`, which returns the last match for a single
        node type, this collects them all: the agent's value from decomposition
        is seeing every step's evidence at once.
        """
        found: list[dict[str, Any]] = []
        for node in nodes:
            if node.type is not NodeType.SUBTASK:
                continue
            output = node.metadata.get("output")
            if isinstance(output, dict) and output.get("findings"):
                found.append(output)
        found.sort(key=lambda item: item.get("index") or 0)
        return tuple(found)

    @staticmethod
    def _sandbox_ref_from(output: Any) -> SandboxRef | None:
        if not isinstance(output, dict) or "id" not in output:
            return None
        return SandboxRef(
            id=output["id"],
            kind=output["kind"],
            root=Path(output["root"]),
            worktree_ref=output.get("worktree_ref"),
        )

    @staticmethod
    def _test_results_from(evaluation: Any) -> tuple[TestResult, ...]:
        """Rebuild the persisted EVALUATION payload into ``TestResult`` objects."""
        if not isinstance(evaluation, dict):
            return ()
        payload = evaluation.get("test_results")
        if not isinstance(payload, list):
            return ()
        return tuple(
            _test_result_from_payload(item)
            for item in payload
            if isinstance(item, Mapping)
        )

    @staticmethod
    def _resources(
        read_output: Any, workspace: Workspace
    ) -> tuple[dict[str, str], ...]:
        """No old nonempty or malformed READ payload has publication authority."""
        if read_output is None or read_output == []:
            return ()
        from copy import deepcopy

        from Sprout.tools.context_keys import _current_node_publication

        scope = _current_node_publication()
        if scope is None or scope.workspace != workspace:
            raise PermissionError("node_artifact_provenance_unavailable")
        records = scope.runtime._node_products.values()
        if not any(record[0] == scope.task and record[2].type is NodeType.READ
                   and record[3] == read_output and record[5] is scope.runtime
                   for record in records):
            raise PermissionError("node_artifact_provenance_unavailable")
        return tuple(deepcopy(read_output))

    @staticmethod
    def _display_path(raw_path: str, workspace: Workspace) -> str:
        try:
            return (
                Path(raw_path)
                .resolve()
                .relative_to(Path(workspace.root).resolve())
                .as_posix()
            )
        except (ValueError, OSError):
            return raw_path

    @staticmethod
    def _agent_prompt(
        task: Task,
        workspace: Workspace,
        resources: tuple[dict[str, str], ...],
        feedback: dict[str, Any] | None = None,
        plan: str = "",
        findings: tuple[dict[str, Any], ...] = (),
    ) -> str:
        """Compose the node-level instruction: task plus only its own resources."""
        if resources or feedback or plan or findings:
            from Sprout.tools.context_keys import _current_node_publication

            if _current_node_publication() is None:
                raise PermissionError("node_artifact_provenance_unavailable")
        lines = [task.instruction]
        lines.extend(["", "Use the resource contents and planning findings supplied below first. "
            "Avoid listing or rereading unchanged files already shown in full; read again when "
            "content is missing, truncated, or freshness matters. Proceed with the requested "
            "implementation rather than asking whether to execute the plan."])
        lines.extend(
            [
                "",
                "For code changes, prefer sandbox_apply_patch with a complete unified diff "
                "so the edit is reviewable and follows the standard patch workflow. Use "
                "sandbox_write_file for creating a complete new file, or the focused "
                "sandbox_edit_file tool for a small exact replacement when a patch is "
                "not practical. Never use process execution to write project files.",
            ]
        )
        if plan:
            lines.extend(["", "Plan from the planning step:", plan])
        if findings:
            lines.extend(["", "Findings from the plan's investigation steps:"])
            for item in findings:
                kind = str(item.get("kind") or "other")
                lines.append(f"- [{kind}] {item.get('description', '')}")
                body = str(item.get("findings") or "").strip()
                if body:
                    lines.append(f"    {body.replace(chr(10), chr(10) + '    ')}")
        if feedback:
            lines.extend(
                [
                    "",
                    "Previous verification feedback:",
                    f"- status: {feedback.get('status', 'unknown')}",
                ]
            )
            failed = feedback.get("failed_commands") or []
            if failed:
                lines.append(f"- failed commands: {', '.join(map(str, failed))}")
            results = feedback.get("results") or []
            for item in results:
                if not isinstance(item, dict):
                    continue
                command = " ".join(map(str, item.get("command", [])))
                lines.append(
                    f"- {command}: exit {item.get('exit_code', '?')} "
                    f"(passed={item.get('passed', False)})"
                )
            test_results = feedback.get("test_results") or []
            for item in test_results:
                if not isinstance(item, dict):
                    continue
                output = str(item.get("output", "")).strip()
                if output:
                    lines.append(f"- {item.get('name', 'test')}: {output}")
            lines.append(
                "If the previous attempt failed verification, inspect the sandbox, "
                "fix the change, and re-run the relevant check before finishing."
            )
        lines.extend(["", "Resources allowed for this step:"])
        if not resources:
            lines.append("- (none)")
        for item in resources:
            lines.append(f"- {item['path']} [{item['decision']}]")
            if item["content"]:
                indented = item["content"].replace("\n", "\n    ")
                lines.append(f"    {indented}")
        return "\n".join(lines)

    @staticmethod
    def _plan_prompt(
        task: Task,
        workspace: Workspace,
        resources: tuple[dict[str, str], ...],
    ) -> str:
        """Ask for a concrete, reviewable plan before any code is written."""
        lines = [
            "Produce a concrete implementation plan for the task below.",
            "Do not modify files yet. Inspect the allowed resources if needed, "
            "then list the exact files, symbols, and edits you intend to make.",
            "Use supplied resource contents first; do not reread unchanged files shown in full. "
            "Read when content is missing, truncated, or freshness matters. Keep the plan concise "
            "and do not ask whether to proceed: the execution step follows this planning step.",
            "",
            "Task:",
            task.instruction,
            "",
            "Resources allowed for planning:",
        ]
        if not resources:
            lines.append("- (none)")
        for item in resources:
            lines.append(f"- {item['path']} [{item['decision']}]")
            if item["content"]:
                indented = item["content"].replace("\n", "\n    ")
                lines.append(f"    {indented}")
        return "\n".join(lines)

    @staticmethod
    def _subtask_prompt(
        description: str,
        metadata: dict,
        resources: tuple[dict[str, str], ...],
    ) -> str:
        """Ask one plan step to gather evidence, explicitly not to edit."""
        lines = [
            "Investigate one step of an implementation plan and report what you "
            "find. This is a read-only pass: do NOT modify any files.",
            "",
            "Step:",
            description,
        ]
        acceptance = str(metadata.get("acceptance") or "").strip()
        if acceptance:
            lines.extend(["", "Done when:", acceptance])
        targets = [str(path) for path in metadata.get("target_paths") or ()]
        if targets:
            lines.extend(["", "Files this step concerns:"])
            lines.extend(f"- {path}" for path in targets)
        lines.extend(
            [
                "",
                "Report the exact files, symbols, and edits the next step should "
                "make. Be concrete: name the functions and the lines that matter.",
                "",
                "Resources available:",
            ]
        )
        if not resources:
            lines.append("- (none)")
        for item in resources:
            lines.append(f"- {item['path']} [{item['decision']}]")
            if item["content"]:
                indented = item["content"].replace("\n", "\n    ")
                lines.append(f"    {indented}")
        return "\n".join(lines)

    @staticmethod
    def _previous_evaluation_feedback(
        node: ExecutionNode, nodes: list[ExecutionNode]
    ) -> dict[str, Any] | None:
        """Return the evaluation output that precedes this agent in the graph."""
        previous_id = node.metadata.get("previous_evaluation_node_id")
        if not isinstance(previous_id, str):
            return None
        for candidate in nodes:
            if candidate.id != previous_id:
                continue
            output = candidate.metadata.get("output")
            return output if isinstance(output, dict) else None
        return None
