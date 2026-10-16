"""Database-only graph settlement fixtures; real commit proof is tested separately."""

import json
from uuid import NAMESPACE_URL, uuid5

import pytest

from Sprout.execution.models import ChangeProposal, ChangeProposalStatus
from Sprout.execution.models import TestResult as Check
from Sprout.gateway.identity import Principal
from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeStatus, NodeType
from Sprout.orchestration.temporal_codec import graph_to_dict
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus
from Sprout.workspace.models import Workspace


async def fixture(tmp_path):
    store = open_metadata_store(str(tmp_path / "metadata.db"))
    workspace = Workspace(id="workspace", root=tmp_path)
    await store.save_workspace(workspace)
    task = Task(id="task", workspace_id=workspace.id, status=TaskStatus.RUNNING,
        actor=Principal(user_id="owner", source="cli", authenticated=True), source="cli",
        metadata={"execution_mode": "code", "replay_plan": {"fingerprint": "replay",
                  "payload": {"schema_version": 5}}})
    await store.save_task(task)
    proposal = ChangeProposal(id="proposal", task_id=task.id, status=ChangeProposalStatus.APPLIED,
        test_results=(Check("test: pytest", True), Check("acceptance:ok: isolated-json", True),
                      Check("poc: isolated-demo", True)),
        metadata={"applied_commit": "b" * 40, "verification": {"status": "passed",
            "acceptance_required": True, "demo_path": "demo.py", "acceptance_spec": {"cases": [
                {"id": "ok", "target": "app:run", "args": [], "expected_output": 1}]}}})
    await store.save_change_proposal(proposal)
    graph_nodes = []
    for suffix, kind in (("read", NodeType.READ), ("agent", NodeType.AGENT),
            ("evaluation", NodeType.EVALUATION), ("agent:1", NodeType.AGENT),
            ("evaluation:1", NodeType.EVALUATION), ("approval", NodeType.APPROVAL),
            ("apply", NodeType.APPLY)):
        node = ExecutionNode(id="task:" + suffix, task_id=task.id, type=kind,
            dependencies=(graph_nodes[-1].id,) if graph_nodes else ())
        graph_nodes.append(node)
        if suffix in {"agent:1", "evaluation:1", "apply"}:
            continue
        waiting = suffix == "approval"
        output = ({"proposal_id": proposal.id, "waiting": True} if waiting else
                  {"status": "passed"} if suffix == "evaluation" else {})
        await store.save_execution_node(ExecutionNode(id=node.id, task_id=task.id, type=kind,
            dependencies=node.dependencies,
            status=NodeStatus.WAITING if waiting else NodeStatus.COMPLETED,
            metadata={"execution_guard": {"owner": suffix, "state": "waiting" if waiting else
                      "completed"}, "output": output}))
    commit_id = str(uuid5(NAMESPACE_URL, "request:commit-apply"))
    output = {"applied": True, "transaction_id": "transaction", "base_commit": "a" * 40,
              "result_commit": "b" * 40, "validation_committed": False}
    await store.save_execution_node(ExecutionNode(id=commit_id, task_id=task.id,
        type=NodeType.APPLY,
        status=NodeStatus.COMPLETED, metadata={"execution_guard": {"owner": "commit-owner",
        "state": "completed"}, "commit_request_fingerprint": "review", "output": output}))
    tx = {"id": "transaction", "task_id": task.id, "node_id": commit_id,
          "proposal_id": proposal.id, "replay_fingerprint": "replay"}
    await store._db.execute("INSERT INTO isolated_git_transactions "
        "(id,proposal_id,workspace_id,owner,state,record_json,result_json,operation) "
        "VALUES (?,?,?,?,?,?,?,?)", ("transaction", proposal.id, workspace.id, "commit-owner",
        "completed", json.dumps(tx), json.dumps({"base_commit": "a" * 40,
        "result_commit": "b" * 40}), "commit"))
    await store.ensure_task_submission({"id": "source-workflow", "task_id": task.id,
        "accepted": True, "state": "resolved", "run_id": "source-run", "payload": graph_to_dict(
            ExecutionGraph(task_id=task.id, nodes=tuple(graph_nodes))),
        "result": {"status": "waiting_approval", "node_id": "task:approval"}})
    return store


async def complete(store):
    return await store.complete_isolated_commit_task("task", "request", "review", "task:evaluation")


async def test_finalizes_original_graph_without_reexecuting(tmp_path):
    store = await fixture(tmp_path)
    try:
        original = (await store.list_task_submissions("task"))[0]
        assert await complete(store)
        assert await complete(store)
        assert (await store.get_task("task")).status == TaskStatus.COMPLETED
        assert (await store.get_execution_node("task:agent:1")).status == NodeStatus.SKIPPED
        assert (await store.get_execution_node("task:approval")).status == NodeStatus.COMPLETED
        assert (await store.get_execution_node("task:apply")).attempts == 0
        assert (await store.list_task_submissions("task"))[0] == original
    finally:
        store.close()


@pytest.mark.parametrize("blocker", ["cancelled", "paused", "evaluation", "unknown", "source"])
async def test_incomplete_or_changed_task_is_not_completed(tmp_path, blocker):
    store = await fixture(tmp_path)
    try:
        if blocker in {"cancelled", "paused"}:
            await store.update_task_status("task", TaskStatus(blocker))
        elif blocker == "evaluation":
            await store._db.execute("UPDATE execution_nodes SET status='failed' WHERE id=?",
                                    ("task:evaluation",))
        elif blocker == "unknown":
            await store.save_execution_node(ExecutionNode(id="unknown", task_id="task",
                type=NodeType.AGENT, status=NodeStatus.WAITING))
        else:
            await store._db.execute("DELETE FROM task_submissions WHERE task_id=?", ("task",))
        assert not await complete(store)
        assert (await store.get_task("task")).status != TaskStatus.COMPLETED
        assert (await store.get_execution_node("task:approval")).status == NodeStatus.WAITING
        assert await store.get_execution_node("task:apply") is None
    finally:
        store.close()


async def test_atomic_rollback_and_database_only_recovery(tmp_path, monkeypatch):
    store = await fixture(tmp_path)
    original = store._db.execute_batch

    async def fail(statements):
        await original([*statements, ("INSERT INTO missing_completion_table VALUES (1)", ())])

    try:
        with monkeypatch.context() as patch:
            patch.setattr(store._db, "execute_batch", fail)
            with pytest.raises(Exception, match="missing_completion_table"):
                await complete(store)
        assert (await store.get_task("task")).status == TaskStatus.RUNNING
        assert (await store.get_execution_node("task:approval")).status == NodeStatus.WAITING
        assert await store.get_execution_node("task:apply") is None
        assert await complete(store)
    finally:
        store.close()


async def test_changed_snapshot_does_not_partially_settle(tmp_path, monkeypatch):
    store = await fixture(tmp_path)
    original = store._db.execute_batch

    async def race(statements):
        await store._db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", ("task",))
        await original(statements)

    try:
        monkeypatch.setattr(store._db, "execute_batch", race)
        assert not await complete(store)
        assert (await store.get_task("task")).status == TaskStatus.CANCELLED
        assert await store.get_execution_node("task:agent:1") is None
        assert (await store.get_execution_node("task:approval")).status == NodeStatus.WAITING
    finally:
        store.close()
