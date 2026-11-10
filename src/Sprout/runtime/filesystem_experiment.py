"""Local filesystem Task assembly over existing guarded nodes and coding backend."""

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.coding_backend import _CODING_PROCESS, _coding_capability
from Sprout.execution.coding_session import CodingNodeSession
from Sprout.execution.filesystem_review import (
    capture_changes,
    checked_changes,
    verification_manifest,
)
from Sprout.execution.host_acceptance import pin_execution_directory
from Sprout.execution.isolated_acceptance import verify_isolated_cases
from Sprout.orchestration.models import (
    ExecutionGraph,
    ExecutionNode,
    NodeBudget,
    NodeStatus,
    NodeType,
)
from Sprout.orchestration.service import SequentialOrchestrator
from Sprout.sandbox.filesystem import FilesystemSandbox, capture_snapshot
from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.security.approval import ApprovalStatus
from Sprout.security.local_diagnostics import report
from Sprout.task.models import TaskStatus
from Sprout.tools.context_keys import _current_project_node
from Sprout.workspace.classifier import classify
from Sprout.workspace.models import ReadPlan, ResourceRef


class FilesystemExperiment:
    automatic = False

    def __init__(self, runtime, task, workspace, reads, image, evidence, *, verification=None):
        self.runtime, self.task, self.workspace = runtime, task, workspace
        self.reads, self.image, self.evidence = reads, image, evidence
        self.sandbox = FilesystemSandbox(workspace.root)
        self.owner = asyncio.current_task()
        self.verification = verification

    async def run(self, runtime, task):
        if (runtime is not self.runtime or asyncio.current_task() is not self.owner
                or task != self.task or task.status is not TaskStatus.CREATED
                or await runtime.get_workspace(task.workspace_id) != self.workspace
                or ((self.workspace.root / ".git").exists()
                    and not getattr(self, "test_only", False))):
            raise PermissionError("filesystem_experiment_foreground_required")
        session = await runtime._sessions.require_owner(task.metadata["session_id"], task.actor)
        if (runtime._sessions.frozen_principal(session) != task.actor
                or runtime.session_workspace_id(session) != self.workspace.id):
            raise PermissionError("filesystem_experiment_subject_changed")
        store = runtime._metadata_or_raise()
        if await store.list_execution_nodes(task.id):
            raise PermissionError("filesystem_experiment_replay_unavailable")
        nodes = []
        kinds = [NodeType.READ, NodeType.SANDBOX, NodeType.AGENT, NodeType.APPROVAL]
        if self.verification:
            kinds.append(NodeType.EVALUATION)
        kinds.append(NodeType.APPLY)
        if getattr(self, "test_only", False):
            kinds = [NodeType.READ, NodeType.SANDBOX, NodeType.EVALUATION]
        for kind in kinds:
            nodes.append(ExecutionNode(
                id=f"{task.id}:{kind.value}", task_id=task.id, type=kind,
                dependencies=(nodes[-1].id,) if nodes else (),
                budget=NodeBudget(max_attempts=1),
                metadata={"tool_names": ["sandbox_read_file", "sandbox_write_file",
                                         "sandbox_edit_file", "isolated_coding_command"]}
                if kind is NodeType.AGENT else {"filesystem_apply": True}
                if kind is NodeType.APPLY else {},
            ))
        graph = ExecutionGraph(task_id=task.id, nodes=tuple(nodes), entry_node_ids=(nodes[0].id,))
        plan = ReadPlan(task_id=task.id, resources=tuple(
            ResourceRef(self.workspace.id, name) for name in self.reads))
        recorder = runtime._filesystem_trajectory(task.id)
        executor = runtime._executor(store)
        handler = executor.handler(task, self.workspace, plan)

        try:
            result = await SequentialOrchestrator(store).execute(
                graph, handler=handler, budget=task.budget, recorder=recorder)
        except BaseException as exc:
            await store.update_task_status(
                task.id, TaskStatus.CANCELLED if isinstance(exc, asyncio.CancelledError)
                else TaskStatus.FAILED)
            runtime._sandbox_lifecycle.close_filesystem(task.id)
            raise

        try:
            await runtime._persist_node_products()
        finally:
            try:
                await runtime._sandbox_lifecycle.finalize(task, result)
            except (OSError, ValueError) as exc:
                runtime._record_security(
                    "filesystem.sandbox_cleanup_failed",
                    {"task_id": task.id, "error": type(exc).__name__},
                )
                result = replace(result, metrics={**result.metrics, "sandbox_cleanup": "failed"})
        return result

    async def allocate(self, scope):
        task, workspace = await self.runtime._project_analysis_subject(scope)
        if task.id != self.task.id or workspace != self.workspace:
            raise PermissionError("filesystem_experiment_subject_changed")
        report(self.runtime, "Sandbox", "preparing: filesystem")
        overrides = self.runtime._file_broker._classify_overrides
        authorize = self._authorize_source(task, workspace)
        ref, receipts = self.sandbox.create(self.reads, authorize, overrides=overrides)
        self.before = {}
        writes = self.runtime._cli_code_writes[task.id][3]
        with pin_execution_directory(workspace.root) as pin:
            if pin.identity != self.sandbox.original_identity:
                raise PermissionError("filesystem_review_workspace_changed")
            for label in writes:
                self.before[label] = self.sandbox.sources.get(label)
                if self.before[label] is None:
                    target = workspace.root / label

                    def check(target=target):
                        pin.check()
                        authorize(target)

                    snapshot = capture_snapshot(workspace.root, target, check, limit=0)
                    if snapshot.reason != "snapshot_not_found":
                        raise PermissionError("filesystem_existing_output_requires_read_selection")
                    self.before[label] = snapshot
        report(self.runtime, "Sandbox", f"ready: {ref.id}")
        return {"id": ref.id, "kind": ref.kind, "root": str(ref.root), "source_receipts": receipts}

    def _authorize_source(self, task, workspace):
        overrides = self.runtime._file_broker._classify_overrides
        paths = task.delegation_scope.bind_paths(str(workspace.root))

        def authorize(path):
            request = ActionRequest(
                task_id=task.id, actor=task.actor, action=ActionType.FILE_READ, scope=paths,
                resource=ResourceRef(workspace.id, path.as_posix(), kind=classify(
                    path.relative_to(workspace.root), overrides=overrides)),
            )
            if (not paths.permits("file.read", path.as_posix())
                    or self.runtime._policy_engine.decide(request).decision
                    is not AccessDecision.ALLOW):
                raise PermissionError("filesystem_experiment_read_denied")

        return authorize

    async def review(self, task, node):
        current = await self._subject(task, node)
        store = self.runtime._metadata_or_raise()
        agent = await store.get_execution_node(node.dependencies[0])
        guard = agent.metadata.get("execution_guard", {}) if agent else {}
        if (agent is None or agent.task_id != current.id or agent.type is not NodeType.AGENT
                or agent.status is not NodeStatus.COMPLETED or guard.get("state") != "completed"):
            raise PermissionError("filesystem_review_agent_evidence_unavailable")
        plan = current.metadata.get("filesystem_plan_v1", {})
        slot = "evaluation-0"
        if "test_repair" in plan.get("actions", ()):
            retry_id = str(uuid5(NAMESPACE_URL, agent.id + ":coding:evaluation-1"))
            if await store.get_coding_execution(retry_id) is not None:
                slot = "evaluation-1"
        execution_id = str(uuid5(NAMESPACE_URL, agent.id + ":coding:" + slot))
        execution = await store.get_coding_execution(execution_id)
        command = None
        if execution:
            if (execution.get("task_id"), execution.get("node_id"), execution.get("owner")) != (
                    current.id, agent.id, guard["owner"]):
                raise PermissionError("filesystem_review_command_evidence_mismatch")
            result = execution.get("result") or {}
            command = {"id": execution_id, "status": execution["state"],
                       "signature": execution["signature"], "exit_code": result.get("exit_code")}
            if ("run_tests" in plan.get("execution", ())
                    and (execution["state"] != "completed" or result.get("exit_code") != 0)):
                raise PermissionError("filesystem_requested_test_command_failed")
            import_id = str(uuid5(NAMESPACE_URL, agent.id + f":coding-import:{slot}-import"))
            imported = await store.get_coding_execution(import_id)
            if imported:
                if (imported.get("task_id"), imported.get("node_id"), imported.get("owner")) != (
                        current.id, agent.id, guard["owner"]):
                    raise PermissionError("filesystem_review_import_evidence_mismatch")
                import_result = imported.get("result") or {}
                command["import"] = {"id": import_id, "signature": imported["signature"],
                                     "status": imported["state"],
                                     "changed_paths": import_result.get("changed_paths", [])}
        if execution is None and "run_tests" in plan.get("execution", ()):
            raise PermissionError("filesystem_requested_test_command_missing")
        report(self.runtime, "ChangeSet", "capturing...")
        review = capture_changes(
            self.sandbox, self.before, current.id, current.workspace_id,
            self._authorize_source(current, self.workspace),
            overrides=self.runtime._file_broker._classify_overrides, command=command,
        )
        return {"filesystem_review": review}

    async def verify(self, task, node):
        current = await self._subject(task, node)
        store = self.runtime._metadata_or_raise()
        review = await store.get_execution_node(node.dependencies[0])
        if (review is None or review.task_id != task.id or review.type is not NodeType.APPROVAL
                or review.status is not NodeStatus.COMPLETED
                or review.metadata.get("execution_guard", {}).get("state") != "completed"):
            raise PermissionError("filesystem_verification_review_unavailable")
        evidence = review.metadata["output"]["filesystem_review"]
        payload = checked_changes(evidence, current.id, current.workspace_id)
        count = len(payload["changes"])
        report(self.runtime, "ChangeSet", f"captured: {count} file(s)")
        report(self.runtime, "Review", f"completed: {count} file(s)")
        if payload["conflicts"] or not payload["changes"]:
            return {"status": "not_run", "verified": False}
        frozen = self.verification
        session = CodingNodeSession(
            store, self, self.runtime._file_broker, evidence=self.evidence,
            original=self.workspace.root, image=self.image, oracle_paths=(".git",),
            sandbox=self.sandbox.ref, task=current, node=node,
        )
        context, manifest = await session.prepare()
        if manifest != verification_manifest(payload):
            raise PermissionError("filesystem_verification_input_changed")

        async def check(manager, actor, workspace, source):
            if (actor, workspace, source) != (current.actor, current.workspace_id, current.source):
                return False
            grant = await self.approval(manager, task=current, node=node)
            return grant.status is ApprovalStatus.APPROVED

        report(self.runtime, "Verification", "started")
        with _coding_capability(check):
            result = await verify_isolated_cases(
                store, self, context=context, node=node,
                acceptance_spec=frozen["specs"]["acceptance"],
                regression_spec=frozen["specs"]["regression"],
                expected_manifest=manifest,
            )
        return {
            "status": result.get("status", "incomplete"),
            "verified": result.get("verified") is True,
            "test_results": result.get("test_results", []),
            "workspace_id": current.workspace_id,
            "task_id": current.id,
            "node_id": node.id,
            "owner": node.metadata["execution_guard"]["owner"],
            "changeset_sha256": evidence["fingerprint"],
            "input_manifest": manifest,
            "image": self.image,
            "specification": {key: value for key, value in frozen.items() if key != "specs"},
            **({key: result[key] for key in ("waiting", "waiting_kind", "pending_approvals")
                if key in result} if result.get("waiting") else {}),
        }

    async def _subject(self, task, node):
        scope = _current_project_node()
        current, workspace = await self.runtime._project_analysis_subject(scope)
        if (current != task or scope.node != node or workspace != self.workspace
                or asyncio.current_task() is not self.owner
                or self.runtime._sandbox_lifecycle.filesystem.get(task.id) is not self):
            raise PermissionError("filesystem_experiment_execution_binding_changed")
        self.sandbox.check()
        return current

    async def approval(self, manager, *, task, node):
        current = await self._subject(task, node)
        if not self.image or manager is not self.runtime.approvals:
            raise PermissionError("filesystem_experiment_container_unavailable")
        automatic = getattr(self, "automatic", False)
        grant = await manager.coding_task_capability(
            current, self.sandbox.ref.id, **({"request_approval": False} if automatic else {})
        )
        if grant.status is ApprovalStatus.PENDING:
            approved = automatic or await self.confirm_tool(grant)
            await self._subject(task, node)
            grant = await manager.decide(
                grant.id, approved, principal=current.actor, decided_by=current.actor.user_id,
                channel="cli", reason="Foreground Filesystem Coding" if automatic else None,
            )
            if automatic and grant.status is ApprovalStatus.APPROVED:
                report(self.runtime, "Approval", f"AUTO-APPROVED: {grant.tool}")
        return grant

    async def run_task_process(self, task_id, command, **kwargs):
        scope = _current_project_node()
        task = await self._subject(scope.task, scope.node)
        binding = _CODING_PROCESS.get()
        if (task_id != task.id or binding is None
                or binding[:3] != (asyncio.current_task(), task_id, tuple(command))):
            raise PermissionError("filesystem_experiment_process_subject_changed")
        return await self.runtime._process_broker.run(
            self.workspace, command, task_id=task_id, actor=task.actor,
            scope=task.delegation_scope.bind_paths(str(self.workspace.root)),
            source=task.source, requested_by=task.actor.user_id, resource_scope=self.workspace.id,
            **kwargs)

    async def authorize_task_process(self, task_id, command):
        return await self.run_task_process(task_id, command, preflight_only=True)

    async def execute(self, *, task, node, command, cwd, command_slot):
        await self._subject(task, node)
        async def check(manager, actor, workspace, source):
            if (actor, workspace, source) != (task.actor, task.workspace_id, task.source):
                return False
            grant = await self.approval(manager, task=task, node=node)
            return grant.status is ApprovalStatus.APPROVED

        session = CodingNodeSession(
            self.runtime._metadata_or_raise(), self, self.runtime._file_broker,
            evidence=Path(self.evidence), original=self.workspace.root, image=self.image,
            oracle_paths=(".git",), sandbox=self.sandbox.ref, task=task, node=node,
        )
        with _coding_capability(check):
            return await session.execute_command(command, cwd=cwd, command_slot=command_slot)
