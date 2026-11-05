"""Opt-in compiled graph/guard smoke; scripted plan/edit, not Runtime/Temporal scheduling."""

import json
import os
from dataclasses import replace
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest

from Sprout.evolution.coding_assembly import build_coding_session_factory
from Sprout.evolution.coding_profile import coding_profile
from Sprout.evolution.isolated_replay import git, prepare
from Sprout.evolution.regression_spec import freeze
from Sprout.evolution.replay_config import configuration_contract
from Sprout.evolution.replay_oracle import snapshot
from Sprout.evolution.replay_plan import build_plan
from Sprout.evolution.replay_snapshot import runtime_snapshot
from Sprout.execution.coding_command_tool import IsolatedCodingCommandTool
from Sprout.execution.coding_commands import PlanBoundCodingCommands
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.isolated_git_evidence import inspect_commit_evidence
from Sprout.execution.isolated_git_plan import IsolatedGitPlan
from Sprout.execution.process_broker import ProcessBroker
from Sprout.gateway.local_identity import creator_record, current_cli_principal
from Sprout.orchestration.compiler import ExecutionGraphBuilder
from Sprout.orchestration.execution_guard import NodeExecutionGuard
from Sprout.orchestration.models import ExecutionNode, NodeType
from Sprout.runtime.nodes import NodeExecutor
from Sprout.skills.registry import SkillRegistry
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus
from Sprout.security.approval import ApprovalManager, ApprovalStatus
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.operational import SqliteOperationalStore
from Sprout.tests.test_coding_docker_integration import (
    FixturePolicy,
    inspect_and_remove_owned_stopped,
)
from Sprout.tools.registry import ToolRegistry
from Sprout.workspace.models import (
    ReadPlan,
    ResourceRef,
    Workspace,
    WorkspaceKind,
    WorkspaceManifest,
)
from Sprout.workspace.read_broker import ReadBroker

IMAGE = os.getenv("SPROUT_CODING_TEST_IMAGE", "")
pytestmark = pytest.mark.skipif(not IMAGE, reason="explicit pinned coding test image required")


@pytest.mark.parametrize("capability", [False, True], ids=["existing-graph", "a2-resume"])
async def test_compiled_graph_stops_at_approval_and_does_not_repeat_docker(tmp_path, capability):
    from Sprout.config.loader import default_settings

    original = tmp_path / "original"
    original.mkdir()
    git(original, "init", "-q")
    (original / "app.py").write_text("def add(a, b): return 0\n", encoding="utf-8", newline="")
    (original / "tests").mkdir()
    (original / "tests/oracle.txt").write_text("host-only-frozen")
    git(original, "add", ".")
    git(original, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", "fixture baseline")
    directory, repo, revision, settings = prepare(original, "HEAD", tmp_path / "experiments",
                                                 default_settings())
    metadata = open_metadata_store(str(directory / "metadata.db"))
    principal = current_cli_principal()
    manifest = WorkspaceManifest(workspace_id="experiment", detected_languages=("python",))
    workspace = Workspace(id="experiment", root=repo, kind=WorkspaceKind.GIT_REPOSITORY,
                          revision=revision, manifest=manifest)
    await metadata.save_workspace(workspace)
    from Sprout.execution.coding_backend import _CODING_PROCESS
    from Sprout.security.access import AccessDecision, ActionType, PolicyDecision

    class Policy(FixturePolicy):
        def decide(self, request):
            decision = super().decide(request)
            context = _CODING_PROCESS.get()
            if (capability and context is not None and context[3] is not None
                    and request.action is ActionType.PROCESS_RUN
                    and decision.decision is AccessDecision.ALLOW):
                return PolicyDecision(AccessDecision.REQUIRE_APPROVAL, "coding fixture requires approval")
            return decision

    policy = Policy()
    broker_calls = []
    process, files = ProcessBroker(policy), FileBroker(policy)
    task = Task(workspace_id=workspace.id, actor=principal, source="cli", status=TaskStatus.RUNNING,
        instruction="Implement integer addition", metadata={"execution_mode": "code"})

    class RuntimeFixture:
        _isolated_coding_required = True
        _settings = settings
        _file_broker = files
        storage = SimpleNamespace(metadata=metadata)
        tools = ToolRegistry()
        skills = SkillRegistry()

        async def run_task_process(self, task_id, command, **kwargs):
            result = await process.run(workspace, command, task_id=task_id, actor=principal,
                                     source="cli", requested_by=principal.user_id,
                                     resource_scope=workspace.id, **kwargs)
            broker_calls.append((command[:3], result.started, result.approval_id))
            return result

        async def authorize_task_process(self, task_id, command):
            return await process.run(workspace, command, task_id=task_id, actor=principal,
                                     source="cli", requested_by=principal.user_id,
                                     resource_scope=workspace.id, preflight_only=True)

    runtime = RuntimeFixture()
    acceptance = {"cases": [{"id": "normal", "target": "app:add", "args": [2, 3],
                              "expected_output": 5}]}
    regression = {"cases": [{"id": "zero", "target": "app:add", "args": [-1, 1],
                              "expected_output": 0}]}
    plan = build_plan(source_task_id="explicit-fixture-source", revision=revision, mode="code",
        original=original, repo=repo, tests=("pytest",), regressions=("pytest",),
        oracle_paths=("tests",), frozen=snapshot(repo, ("tests",)), image=IMAGE,
        configuration=configuration_contract(settings), runtime=runtime_snapshot(runtime),
        creator=creator_record(principal), coding=coding_profile(IMAGE))
    task = replace(task, metadata={**task.metadata, "replay_plan": plan,
        "replay_source_task_id": "explicit-fixture-source", "acceptance_spec": acceptance,
        "require_acceptance_tests": True, "regression_fingerprint": freeze(directory, regression)})
    await metadata.save_task(task)
    read = ReadPlan(task_id=task.id, resources=(ResourceRef(workspace.id, str(repo / "app.py")),))
    graph = ExecutionGraphBuilder().build(task, read, manifest)
    for node in graph.nodes:
        await metadata.save_execution_node(node)
    factory = build_coding_session_factory(runtime, task)
    commands = PlanBoundCodingCommands(runtime, task)
    executor = NodeExecutor(metadata=metadata, router=None, contexts=None,
        tools=runtime.tools, skills=runtime.skills, read_broker=ReadBroker(policy),
        file_broker=files, process_broker=process, apply_broker=None,
        isolated_coding_required=True, coding_session_factory=factory)
    guard = NodeExecutionGuard(metadata)
    approvals_path = directory / "a2-approvals.db"
    database = SqliteDatabase(approvals_path) if capability else None
    manager = ApprovalManager(SqliteOperationalStore(database)) if database else None
    identities = []
    handler_calls = []
    for node in graph.nodes:
        slots = {NodeType.TEST_DESIGN: ("baseline-acceptance-0",),
                 NodeType.AGENT: ("evaluation-0",),
                 NodeType.EVALUATION: ("evaluation-acceptance-0", "evaluation-regression-0",
                                       "evaluation-poc-0")}.get(node.type, ())
        identities.extend(str(uuid5(NAMESPACE_URL, node.id + ":coding:" + slot)) for slot in slots)

    async def handler(node):
        handler_calls.append(node.id)
        if node.type == NodeType.PLAN:
            return {"plan": "Fixed fixture: implement addition; no production model"}
        if node.type == NodeType.AGENT:
            if capability:
                grant = await commands.approval(manager, task=task, node=node)
                if grant.status is ApprovalStatus.PENDING:
                    return {"waiting": True, "pending_approvals": [grant.id]}
                assert grant.status is ApprovalStatus.APPROVED
            nodes = await metadata.list_execution_nodes(task.id)
            sandbox = executor._sandbox_ref_from(executor._output_for(nodes, NodeType.SANDBOX))
            script = ("from pathlib import Path; "
                      "Path('app.py').write_text('def add(a,b): return a+b\\n'); print('coding-node-ok')")
            tool = IsolatedCodingCommandTool(commands, task=task, node=node, sandbox=sandbox)
            output = await tool.invoke({"command": ["python", "-c", script]})
            assert output.ok and output.data["imported"] is True
            return {"content": output.content, "result": output.data}
        return await executor.execute_node(node, workspace, read, task)

    try:
        outcomes = []
        for node in graph.nodes:
            assert all(any(item["node_id"] == dep and item["status"] == "completed"
                           for item in outcomes) for dep in node.dependencies)
            result = await guard.execute(node, handler)
            outcomes.append(result)
            if result["status"] == "waiting":
                break
        if capability:
            parked = outcomes[-1]
            assert parked["type"] == "agent" and parked["status"] == "waiting", outcomes
            approval_id, = parked["output"]["pending_approvals"]
            await metadata.update_task_status(task.id, TaskStatus.WAITING_APPROVAL)
            database.close()
            database = SqliteDatabase(approvals_path)
            manager = ApprovalManager(SqliteOperationalStore(database))
            runtime.approvals = manager
            process._approvals = manager
            guard = NodeExecutionGuard(metadata)
            commands = PlanBoundCodingCommands(runtime, task)
            await manager.decide(approval_id, True, principal=principal,
                                 decided_by=principal.user_id)
            await metadata.update_task_status(task.id, TaskStatus.RUNNING)
            waiting = await metadata.get_execution_node(parked["node_id"])
            first = await guard.execute(waiting, handler, resume_key=approval_id)
            assert first["status"] == "completed", first
            second = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
            await metadata.save_execution_node(second)
            identities.append(str(uuid5(NAMESPACE_URL, second.id + ":coding:evaluation-0")))
            followup = await guard.execute(second, handler)
            assert followup["status"] == "completed", followup
            assert len(await manager.store.list_approvals()) == 1
            before = list(handler_calls)
            assert await guard.execute(waiting, handler, resume_key=approval_id) == first
            assert await guard.execute(second, handler) == followup
            assert handler_calls == before
            results = []
            for node_id in (waiting.id, second.id):
                identity = str(uuid5(NAMESPACE_URL, node_id + ":coding:evaluation-0"))
                persisted = await metadata.get_coding_execution(identity)
                assert persisted["state"] == "completed"
                result = persisted["result"]
                assert result["started"] is True and result["exit_code"] == 0
                results.append({"node_id": node_id, "exit_code": result["exit_code"],
                                "stdout": result["stdout"]})
            launches = [call for call in broker_calls if call[0][:2] == ("docker", "run") and call[1]]
            assert len(launches) == 3  # Baseline verification plus two coding nodes.
            assert not any(call[2] for call in broker_calls)
            print(json.dumps({"task_id": task.id, "approval_id": approval_id,
                              "approvals": 1, "docker_runs": 2, "results": results},
                             default=str))
            return
        assert outcomes[-1]["type"] == "approval", outcomes[-1]
        assert outcomes[-1]["status"] == "waiting"
        await metadata.update_task_status(task.id, TaskStatus.WAITING_APPROVAL)
        proposals = await metadata.list_change_proposals(task.id)
        assert len(proposals) == 1 and proposals[0].status.value == "pending", outcomes
        assert proposals[0].files_changed == ("app.py",)
        evaluation = next(item for item in outcomes if item["type"] == "evaluation")
        assert evaluation["output"]["verification_report"]["verified"] is True
        planner = IsolatedGitPlan(workspace, original=original, evidence=directory)
        git_plan = await planner.commit_plan(proposals[0].sandbox_ref.root)
        evidence = await inspect_commit_evidence(metadata, task_id=task.id,
            evaluation_node_id=evaluation["node_id"], planner=planner, git_plan=git_plan)
        assert evidence["evidence_binding_passed"] and not evidence["write_authorized"]
        before = [await metadata.get_coding_execution(identity) for identity in identities]
        first_calls = list(handler_calls)
        for node in graph.nodes:
            cached = await guard.execute(node, handler)
            if cached["status"] == "waiting":
                break
        assert [await metadata.get_coding_execution(identity) for identity in identities] == before
        assert handler_calls == first_calls
        assert len(await metadata.list_change_proposals(task.id)) == 1
        assert (await metadata.get_task(task.id)).status == TaskStatus.WAITING_APPROVAL
        apply_result = await executor._apply(workspace, task)
        assert apply_result["reason"] == "isolated_apply_adapter_required"
        assert (repo / "app.py").read_text() == "def add(a, b): return 0\n"
        assert (original / "app.py").read_text() == "def add(a, b): return 0\n"
        (directory / "graph-smoke.json").write_text(json.dumps({
            "task_id": task.id, "node_types": [item["type"] for item in outcomes],
            "status": "waiting_approval", "container_records": len(before),
            "evidence_binding_passed": evidence["evidence_binding_passed"],
            "cached_reentry_handler_calls": len(handler_calls) - len(first_calls),
            "scope": "compiled graph/guard fixture; not Runtime/Temporal or production model",
        }, indent=2), encoding="utf-8")
    finally:
        try:
            for identity in identities:
                record = await metadata.get_coding_execution(identity)
                if record and record["state"] == "completed":
                    command = record["command"]
                    inspect_and_remove_owned_stopped(
                        command[command.index("--name") + 1], directory)
        finally:
            if database is not None:
                database.close()
            metadata.close()
