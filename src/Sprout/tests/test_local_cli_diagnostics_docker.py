"""Real diagnostic Coding command + Broker; deterministic plan fixture, not CLI Task scheduling."""
import json
import os
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

import pytest

from Sprout.config.settings import Settings
from Sprout.evolution.coding_profile import coding_profile
from Sprout.evolution.replay_config import configuration_contract
from Sprout.evolution.replay_plan import build_plan
from Sprout.evolution.replay_snapshot import runtime_snapshot
from Sprout.execution.coding_command_tool import IsolatedCodingCommandTool
from Sprout.execution.coding_commands import PlanBoundCodingCommands
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.process_broker import ProcessBroker
from Sprout.message.models import Message
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.rootstock.backends.sqlite_store import open_session_store
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.security.local_diagnostics import (
    DiagnosticApprovals,
    LocalCLIDiagnostics,
    coding_approval,
)
from Sprout.session.manager import SessionManager
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.operational import SqliteOperationalStore
from Sprout.tests.test_coding_assembly import fixture
from Sprout.tests.test_coding_docker_integration import (
    FixturePolicy,
    inspect_and_remove_owned_stopped,
)
from Sprout.tools.context_keys import _project_node_scope

IMAGE = os.getenv("SPROUT_CODING_TEST_IMAGE", "")


@pytest.mark.skipif(not IMAGE, reason="explicit existing pinned coding test image required")
async def test_real_diagnostic_coding_without_pending_approval(tmp_path, monkeypatch):
    # The existing assembly fixture provides a real Git worktree and plan.
    # Its prior verification outputs are synthetic; only the command below
    # claims real Docker execution. No verification or final Apply is approved.
    store, runtime, task, prior, sandbox = await fixture(tmp_path, monkeypatch)
    mode = LocalCLIDiagnostics(Settings())
    runtime._settings = mode.settings
    sessions = open_session_store(str(mode.root / "session.db"))
    runtime._sessions = SessionManager(sessions)
    runtime.get_task = store.get_task
    session = await runtime._sessions.resolve(Message("", channel="cli"), principal=task.actor)
    old = task.metadata["replay_plan"]["payload"]
    plan = build_plan(source_task_id=old["source_task_id"], revision=old["revision"], mode="code",
        original=old["original_root"], repo=old["repository_root"], tests=old["test_commands"],
        regressions=old["regression_commands"], oracle_paths=old["oracle_paths"],
        frozen=old["oracle_snapshot"], image=IMAGE,
        configuration=configuration_contract(mode.settings), runtime=runtime_snapshot(runtime),
        creator=old["creator"], coding=coding_profile(IMAGE))
    task = replace(task, metadata={**task.metadata, "session_id": session.id, "replay_plan": plan})
    await store.save_task(task)
    await store.finish_execution_node(replace(prior, status=NodeStatus.COMPLETED),
                                      "new-owner", "completed")
    sandbox_node = ExecutionNode(task_id=task.id, type=NodeType.SANDBOX)
    await store.save_execution_node(sandbox_node)
    assert await store.claim_execution_node(sandbox_node, "sandbox-owner", "")
    sandbox_node = await store.get_execution_node(sandbox_node.id)
    await store.finish_execution_node(replace(sandbox_node, status=NodeStatus.COMPLETED,
        metadata={**sandbox_node.metadata, "output": {"id": sandbox.id, "kind": sandbox.kind,
                  "root": str(sandbox.root), "worktree_ref": sandbox.worktree_ref}}),
        "sandbox-owner", "completed")
    node = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
    await store.save_execution_node(node)
    assert await store.claim_execution_node(node, "diagnostic-owner", "")
    node = await store.get_execution_node(node.id)
    database = SqliteDatabase(mode.root / "audit.db")
    manager = DiagnosticApprovals(SqliteOperationalStore(database), diagnostics=mode)
    runtime.approvals = manager
    class Policy(FixturePolicy):
        def decide(self, request):
            decision = super().decide(request)
            if (request.action is ActionType.PROCESS_RUN
                    and decision.decision is AccessDecision.ALLOW):
                return PolicyDecision(AccessDecision.REQUIRE_APPROVAL, "fixture coding approval")
            return decision
    policy = Policy()
    process = ProcessBroker(policy, approvals=manager)
    runtime._file_broker = FileBroker(policy, approvals=manager)
    workspace = await store.get_workspace(task.workspace_id)
    calls = []
    async def run(task_id, command, **kwargs):
        result = await process.run(workspace, command, task_id=task_id, actor=task.actor,
            requested_by=task.actor.user_id, source="cli", resource_scope=workspace.id,
            scope=task.delegation_scope, **kwargs)
        calls.append((tuple(command), result))
        return result
    async def authorize(task_id, command):
        return await run(task_id, command, preflight_only=True)
    runtime.run_task_process, runtime.authorize_task_process = run, authorize
    commands = PlanBoundCodingCommands(runtime, task)
    tool = IsolatedCodingCommandTool(commands, task=task, node=node, sandbox=sandbox)
    identity = str(uuid5(NAMESPACE_URL, node.id + ":coding:evaluation-0"))
    try:
        async with mode.foreground(runtime):
            await mode.bind(runtime, session.id, task.actor)
            with _project_node_scope(runtime, task, workspace, node) as scope:
                with coding_approval(manager, tool, scope):
                    record = await commands.approval(manager, task=task, node=node)
                assert record.status == "approved"
                result = await tool.invoke({
                    "command": ["python", "-c", "print('diagnostic-docker-ok')"]})
                assert result.ok, result.to_text()
                assert "diagnostic-docker-ok" in result.to_text()
                repeat = await tool.invoke({
                    "command": ["python", "-c", "print('must-not-repeat')"]})
                assert not repeat.ok
            records = await manager.store.list_approvals()
            assert len(records) == 1 and records[0].status == "approved"
            launches = [r for argv, r in calls if argv[:2] == ("docker", "run") and r.started]
            assert len(launches) == 1 and launches[0].exit_code == 0
            assert not any(r.approval_id for _, r in calls)
            print(json.dumps({"task_id": task.id, "node_id": node.id, "approval_id": record.id,
                "human_prompts": 0, "pending_records": 0, "docker_runs": len(launches),
                "exit_code": launches[0].exit_code, "stdout": launches[0].stdout}))
        assert (await manager.store.get_approval(record.id)).status == "consumed"
    finally:
        execution = await store.get_coding_execution(identity)
        if execution and execution["state"] == "completed":
            argv = execution["command"]
            inspect_and_remove_owned_stopped(argv[argv.index("--name") + 1], tmp_path)
        database.close()
        sessions.close()
        store.close()
