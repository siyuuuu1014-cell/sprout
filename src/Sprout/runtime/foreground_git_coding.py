"""Task-local assembly for the foreground Git v2 execution graph."""

import asyncio
import re
import shutil
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.acceptance_producer import check_git_coverage, check_preparation
from Sprout.execution.coding_backend import _CODING_PROCESS, _coding_capability
from Sprout.execution.coding_command_tool import IsolatedCodingCommandTool
from Sprout.execution.coding_commands import PlanBoundCodingCommands
from Sprout.execution.coding_session import CodingNodeSession
from Sprout.execution.docker_verification import validate_image
from Sprout.execution.git_acceptance_binding import capture_installation, capture_repository
from Sprout.execution.git_s1 import _snapshot
from Sprout.execution.host_acceptance import binding_subject, pin_execution_directory
from Sprout.execution.isolated_acceptance import verify_isolated_cases
from Sprout.execution.models import SandboxRef
from Sprout.execution.node_result_source import payload_digest
from Sprout.orchestration.models import NodeStatus, NodeType
from Sprout.sandbox.isolated_git_worktree import IsolatedGitWorktreeSandbox
from Sprout.security.approval import ApprovalStatus
from Sprout.security.local_diagnostics import coding_approval, report
from Sprout.tools.context_keys import _current_project_node
from Sprout.workspace.authorized_content import relative_label


class ForegroundGitCoding:
    automatic = True

    def __init__(self, runtime, task, workspace, *, reviewed=False):
        self.runtime, self.task, self.workspace = runtime, task, workspace
        self.owner = asyncio.current_task()
        self.store = runtime.storage.metadata
        self.plan = task.metadata["coding_preparation_v1"]
        self.paths = tuple(dict.fromkeys((*self.plan["reads"], *self.plan["writes"])))
        self.bundle = task.metadata["git_acceptance_v1"]
        self.binding = task.metadata["host_acceptance_v2"]
        self.acceptance, self.regression = check_preparation(
            self.bundle, task.instruction, self.plan,
        )
        check_git_coverage(self.acceptance, task.instruction)
        self.image = self.plan["coding_image"]
        validate_image(self.image)
        self.root = Path(self.binding["artifacts"]["root"])
        self.provider = IsolatedGitWorktreeSandbox(workspace)
        self.sandbox = None
        self.commands = PlanBoundCodingCommands(runtime, task, factory=self.session)
        self.commands.foreground_git = self
        self.check_binding(allow_index_changes=reviewed)

    def check_binding(self, *, allow_index_changes=False):
        binding = self.binding
        executable = shutil.which("git")
        repository = capture_repository(self.workspace.root)
        expected_repository = dict(binding["repository"])
        if allow_index_changes:
            expected_repository["index_sha256"] = repository["index_sha256"]
        if (not executable or binding["version"] != 2
                or binding["subject"] != binding_subject(self.task, self.workspace)
                or binding["bundle_sha256"] != self.bundle["bundle_sha256"]
                or binding["sha256"] != payload_digest(
                    {key: value for key, value in binding.items() if key != "sha256"})
                or repository != expected_repository
                or capture_installation(self.workspace.root, executable)
                != binding["git_installation"]):
            raise PermissionError("foreground_git_binding_changed")
        with pin_execution_directory(self.root) as pin:
            artifacts = binding["artifacts"]
            if list(pin.identity) != artifacts["identity"]:
                raise PermissionError("foreground_git_artifacts_changed")
            for name, field in (("frozen-commands.json", "test_sha256"),
                                ("frozen-demo.py", "demo_sha256")):
                snapshot = _snapshot(self.root, name, pin.check, 8_000_000)
                if (snapshot is None or snapshot.digest != self.bundle[field]
                        or artifacts["resources"].get(name) != {
                            "digest": snapshot.digest, "identity": asdict(snapshot.identity)}):
                    raise PermissionError("foreground_git_artifacts_changed")

    async def subject(self, node):
        scope = _current_project_node()
        if (scope is None or scope.node.id != node.id or scope.runtime is not self.runtime
                or asyncio.current_task() is not self.owner):
            raise PermissionError("foreground_git_node_owner_required")
        task, workspace = await self.runtime._project_analysis_subject(scope)
        if replace(task, status=self.task.status) != self.task or workspace != self.workspace:
            raise PermissionError("foreground_git_subject_changed")
        return task

    async def approval(self, manager, *, task, node):
        current = await self.subject(node)
        if manager is not self.runtime.approvals or current != task:
            raise PermissionError("foreground_git_coding_approval_subject_changed")
        grant = await manager.coding_task_capability(
            current, self.commands._binding, request_approval=False,
        )
        if grant.status is ApprovalStatus.PENDING:
            if await self.subject(node) != current:
                raise PermissionError("foreground_git_coding_approval_subject_changed")
            grant = await manager.decide(
                grant.id, True, principal=current.actor, decided_by=current.actor.user_id,
                channel="cli", reason="Foreground Git isolated Coding",
            )
            if grant.status is ApprovalStatus.APPROVED:
                report(self.runtime, "Approval", f"AUTO-APPROVED: {grant.tool}")
        return grant

    async def allocate(self, node):
        await self.subject(node)
        self.check_binding()
        self.sandbox = await self.provider.create()
        root, git_dir = self.provider._check_git_root(self.sandbox.root)
        with pin_execution_directory(root) as pin:
            for path, digest in self.bundle["source_files"].items():
                snapshot = _snapshot(root, path, pin.check, 1_000_000)
                if snapshot is None or snapshot.digest != digest:
                    raise PermissionError("foreground_git_checkout_differs_from_frozen_inputs")
        if not git_dir.is_dir():
            raise PermissionError("foreground_git_worktree_missing")
        await self.runtime._register_frozen_sandbox(self.sandbox)
        return self.sandbox

    def session(self, *, task, node, sandbox):
        if sandbox != self.sandbox or task.id != self.task.id:
            raise PermissionError("foreground_git_sandbox_mismatch")
        return ForegroundGitSession(
            self, self.store, self, self.runtime._file_broker, evidence=self.root,
            original=self.workspace.root, image=self.image, oracle_paths=(".git",),
            sandbox=sandbox, task=task, node=node, regression_spec=self.regression.to_payload(),
            selected_paths=self.paths,
        )

    async def run_task_process(self, task_id, command, **kwargs):
        frame = _current_project_node()
        if frame is None:
            raise PermissionError("foreground_git_node_owner_required")
        task = await self.subject(frame.node)
        binding = _CODING_PROCESS.get()
        if (binding is None or binding[:3] != (self.owner, task_id, tuple(command))
                or task.id != task_id
                or "process.run" not in task.delegation_scope.allowed_actions):
            raise PermissionError("foreground_git_process_binding_changed")
        scope = replace(task.delegation_scope, allowed_paths=(".",),
                        allowed_actions=frozenset({"process.run"})).bind_paths(str(self.workspace.root))
        return await self.runtime._process_broker.run(
            self.workspace, command, cwd=self.workspace.root, task_id=task_id, actor=task.actor,
            source=task.source, requested_by=task.actor.user_id, resource_scope=self.workspace.id,
            session_id=task.metadata["session_id"], scope=scope, **kwargs,
        )

    async def authorize_task_process(self, task_id, command):
        return await self.run_task_process(task_id, command, preflight_only=True)

    async def _evaluation_manifest(self, evaluation):
        measured = evaluation.metadata.get("output") or {}
        intent_id = str(uuid5(NAMESPACE_URL, evaluation.id + ":case-plan:evaluation"))
        intent = await self.store.get_coding_intent(intent_id) or {}
        contract = intent.get("contract") or {}
        manifest = contract.get("input_manifest")
        acceptance = self.acceptance.to_payload()
        if (intent.get("task_id") != self.task.id or intent.get("node_id") != evaluation.id
                or intent.get("owner") != evaluation.metadata["execution_guard"].get("owner")
                or contract != {"acceptance": acceptance,
                                "regression": self.regression.to_payload(), "phase": "evaluation",
                                "input_manifest": manifest, "image": self.image}
                or measured.get("acceptance_spec") != acceptance
                or type(manifest) is not dict or not manifest
                or not set(manifest) <= set(self.paths)
                or not all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
                           for value in manifest.values())):
            raise PermissionError("foreground_git_review_execution_inputs_changed")
        return manifest

    async def restore_review(self):
        """Recover the exact settled review and its independent input contract."""
        nodes = await self.store.list_execution_nodes(self.task.id)
        completed = {
            node.id: node for node in nodes if node.task_id == self.task.id
            and node.status is NodeStatus.COMPLETED
            and node.metadata.get("execution_guard", {}).get("state") == "completed"
        }
        review = completed.get(f"{self.task.id}:approval")
        sandbox = completed.get(f"{self.task.id}:sandbox")
        if (review is None or review.type is not NodeType.APPROVAL
                or sandbox is None or sandbox.type is not NodeType.SANDBOX):
            raise PermissionError("foreground_git_review_not_settled")
        output = review.metadata.get("output") or {}
        evaluation = completed.get(output.get("evaluation_node_id"))
        if evaluation is None or evaluation.type is not NodeType.EVALUATION:
            raise PermissionError("foreground_git_review_verification_missing")
        measured = evaluation.metadata.get("output") or {}
        if (output.get("status") != "reviewed" or measured.get("verified") is not True
                or measured.get("status") != "passed"
                or output.get("verification_sha256") != payload_digest(measured)
                or output.get("test_results") != measured.get("test_results")
                or output.get("bundle_sha256") != self.bundle["bundle_sha256"]
                or output.get("git_baseline") != self.binding["repository"]["revision"]):
            raise PermissionError("foreground_git_review_verification_changed")
        manifest = output.get("input_manifest")
        paths = output.get("files_changed")
        patch = output.get("diff")
        if (manifest != await self._evaluation_manifest(evaluation)
                or type(paths) is not list or not paths or not all(type(p) is str for p in paths)
                or len({p.casefold() for p in paths}) != len(paths)
                or not set(paths) <= set(self.plan["writes"])
                or type(patch) is not str or not patch.startswith("diff --git ")):
            raise PermissionError("foreground_git_review_plan_changed")
        diff_digest = sha256(patch.encode("utf-8")).hexdigest()
        if output.get("review_diff_sha256", diff_digest) != diff_digest:
            raise PermissionError("foreground_git_review_diff_changed")
        source = self.bundle["source_files"]
        changes = {p for p in source.keys() | manifest.keys() if source.get(p) != manifest.get(p)}
        if changes != set(paths) or any(relative_label(p) != p for p in changes):
            raise PermissionError("foreground_git_review_manifest_changed")

        ref = sandbox.metadata.get("output") or {}
        if ref.get("kind") != "git_worktree" or not all(
                type(ref.get(key)) is str and ref[key] for key in ("id", "root", "worktree_ref")):
            raise PermissionError("foreground_git_review_sandbox_missing")
        return SandboxRef(ref["id"], ref["kind"], Path(ref["root"]), ref["worktree_ref"]), output

    async def review(self, node):
        task = await self.subject(node)
        self.check_binding()
        evaluations = [
            item for item in await self.store.list_execution_nodes(task.id)
            if item.type is NodeType.EVALUATION and item.status is NodeStatus.COMPLETED
            and item.metadata.get("execution_guard", {}).get("state") == "completed"
            and (item.metadata.get("output") or {}).get("verified") is True
        ]
        if not evaluations:
            raise PermissionError("foreground_git_verified_evaluation_required")
        evaluation = evaluations[-1]
        output = evaluation.metadata["output"]
        manifest = await self._evaluation_manifest(evaluation)
        paths = await self.provider.changed_files(self.sandbox)
        if not paths or not set(paths) <= set(self.plan["writes"]):
            raise PermissionError("foreground_git_review_changes_outside_plan")
        await self.runtime._git_broker._authorize_review(
            _current_project_node(), paths, self.sandbox.root,
        )
        source = self.bundle["source_files"]
        patch = await self.provider.review_patch(
            self.sandbox, self.binding["repository"]["revision"],
            tracked=tuple(path for path in paths if path in source),
            added=tuple(path for path in paths if path not in source),
        )
        if not patch:
            raise PermissionError("foreground_git_review_evidence_changed")
        with pin_execution_directory(self.sandbox.root) as pin:
            for path in source.keys() | manifest.keys():
                snapshot = _snapshot(self.sandbox.root, path, pin.check, 1_000_000)
                if (snapshot.digest if snapshot else None) != manifest.get(path):
                    raise PermissionError("foreground_git_review_candidate_changed")
        if set(paths) != {path for path in source.keys() | manifest.keys()
                          if source.get(path) != manifest.get(path)}:
            raise PermissionError("foreground_git_review_manifest_mismatch")
        report(self.runtime, "Review", f"completed: {len(paths)} modified file(s)")
        return {"status": "reviewed", "files_changed": list(paths), "diff": patch,
                "review_diff_sha256": sha256(patch.encode("utf-8")).hexdigest(),
                "git_baseline": self.binding["repository"]["revision"],
                "bundle_sha256": self.bundle["bundle_sha256"], "input_manifest": manifest,
                "evaluation_node_id": evaluation.id, "verification_sha256": payload_digest(output),
                "test_results": output["test_results"]}


class ForegroundGitSession(CodingNodeSession):
    def __init__(self, assembly, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.assembly = assembly

    async def prepare(self, *, repair=False):
        await self.assembly.subject(self.node)
        self.assembly.check_binding()
        self.assembly.provider._check_git_root(self.sandbox.root)
        return await super().prepare(repair=repair)

    async def _verified_call(self, callback, **kwargs):
        assembly = self.assembly
        task = await assembly.subject(self.node)
        tool = IsolatedCodingCommandTool(
            assembly.commands, task=task, node=self.node, sandbox=self.sandbox,
            planned_paths=assembly.paths,
        )

        async def check(manager, actor, workspace, source):
            if (manager is not assembly.runtime.approvals
                    or (actor, workspace, source) != (task.actor, task.workspace_id, task.source)):
                return False
            grant = await assembly.approval(manager, task=task, node=self.node)
            return grant.status is ApprovalStatus.APPROVED

        with coding_approval(assembly.runtime.approvals, tool, _current_project_node()):
            with _coding_capability(check):
                return await callback(**kwargs)

    async def verify(self, *, acceptance_spec, regression_spec, phase="evaluation"):
        if (acceptance_spec != self.assembly.acceptance.to_payload()
                or phase == "evaluation" and regression_spec != self.regression_spec):
            raise PermissionError("foreground_git_frozen_spec_changed")
        async def measure():
            context, manifest = await self.prepare()
            result = await verify_isolated_cases(
                self.metadata, self.runner, context=context, node=self.node,
                acceptance_spec=acceptance_spec, regression_spec=regression_spec,
                phase=phase, expected_manifest=manifest,
            )
            if phase == "evaluation" and not result.get("waiting"):
                source = self.assembly.bundle["source_files"]
                result["repair"] = {
                    "round": self.node.metadata.get("foreground_repair_round", 0),
                    "task_id": self.node.task_id,
                    "node_id": self.node.id,
                    "bundle_sha256": self.assembly.bundle["bundle_sha256"],
                    "candidate_manifest": manifest,
                    "changed_files": sorted(path for path in source.keys() | manifest.keys()
                                            if source.get(path) != manifest.get(path)),
                }
            return result

        return await self._verified_call(measure)

    async def verify_auxiliary(self, command_metadata):
        return await self._verified_call(
            super().verify_auxiliary, command_metadata=command_metadata,
        )
