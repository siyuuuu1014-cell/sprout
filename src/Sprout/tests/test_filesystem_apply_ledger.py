import asyncio
from dataclasses import asdict, replace

import pytest

from Sprout.execution.filesystem_review import capture_changes, verification_manifest
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.gateway.identity import Principal
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.sandbox.filesystem import FilesystemSandbox, capture_snapshot
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus
from Sprout.workspace.models import Workspace


@pytest.mark.parametrize(
    "mode", ["completed", "partial", "interrupted", "unknown", "missing_review",
             "missing_evaluation", "cross_task", "wrong_plan", "wrong_session", "wrong_workspace"],
)
async def test_filesystem_apply_ledger_claim_and_reopen(tmp_path, mode):
    root = tmp_path / "source"
    root.mkdir()
    (root / "app.py").write_text("before", encoding="utf-8")
    workspace = Workspace("workspace", root)
    actor = Principal("owner", authenticated=True, source="cli")
    task = Task(workspace_id=workspace.id, actor=actor, source="cli",
                status=TaskStatus.RUNNING,
                metadata={"filesystem_experiment": True, "session_id": "session"})
    with FilesystemSandbox(root) as sandbox:
        ref, _ = sandbox.create(("app.py",), lambda _: None)
        before = dict(sandbox.sources)
        before["test_app.py"] = capture_snapshot(root, root / "test_app.py", lambda: None)
        (ref.root / "app.py").write_text("after", encoding="utf-8")
        (ref.root / "test_app.py").write_text("new", encoding="utf-8")
        review = capture_changes(sandbox, before, task.id, workspace.id, lambda _: None)
    digest = review["fingerprint"]
    payload = review["payload"]
    plan = {"version": 1, "files": [{
        "path": item["path"], "identity": item["before"]["identity"] if item["before"] else None,
        "before": item["before"]["sha256"] if item["before"] else None,
        "after": item["after"]["sha256"],
    } for item in payload["changes"]]}
    db = str(tmp_path / "state.db")
    store = open_metadata_store(db)
    other = open_metadata_store(db)
    await store.save_workspace(workspace)
    await store.save_task(task)
    try:
        for kind, output in (
            (NodeType.APPROVAL, {"filesystem_review": review}),
            (NodeType.EVALUATION, {
                "task_id": task.id if mode != "cross_task" else "other",
                "workspace_id": workspace.id, "node_id": f"{task.id}:evaluation",
                "owner": "guard", "verified": True, "status": "passed",
                "changeset_sha256": digest, "input_manifest": verification_manifest(payload),
                "test_results": [{"passed": True, "executed": True}],
            }),
            (NodeType.APPLY, None),
        ):
            if (kind is NodeType.APPROVAL and mode == "missing_review"
                    or kind is NodeType.EVALUATION and mode == "missing_evaluation"):
                continue
            prior = {NodeType.EVALUATION: NodeType.APPROVAL, NodeType.APPLY: NodeType.EVALUATION}
            dependencies = (f"{task.id}:{prior[kind].value}",) if kind in prior else ()
            node = ExecutionNode(id=f"{task.id}:{kind.value}", task_id=task.id, type=kind,
                                 dependencies=dependencies)
            await store.save_execution_node(node)
            assert await store.claim_execution_node(node, "guard", "")
            if output is not None:
                active = await store.get_execution_node(node.id)
                settled = replace(active, status=NodeStatus.COMPLETED,
                                  metadata={**active.metadata, "output": output})
                assert await store.finish_execution_node(settled, "guard", "completed")
        if mode == "wrong_plan":
            plan["files"][0]["after"] = "0" * 64
        record = {"version": 3, "id": "txn", "proposal_id": f"filesystem:{task.id}:{digest}",
                  "task_id": task.id, "workspace_id": workspace.id,
                  "node_id": f"{task.id}:apply", "owner": "guard", "target": str(root),
                  "principal": asdict(actor), "plan": plan, "plan_sha256": fingerprint(plan),
                  "session_id": "session", "approval_id": "test-reference"}
        if mode == "wrong_session":
            record["session_id"] = "unrelated"
        if mode == "wrong_workspace":
            record["workspace_id"] = "unrelated"
        claims = await asyncio.gather(store.claim_apply_transaction(record, digest),
                                      other.claim_apply_transaction(record, digest))
        if mode in {"missing_review", "missing_evaluation", "cross_task", "wrong_plan",
                    "wrong_session", "wrong_workspace"}:
            assert claims == [False, False]
            assert await store.get_apply_transaction("txn") is None
            return
        assert sorted(claims) == [False, True]
        for item in plan["files"] if mode == "completed" else plan["files"][:1]:
            assert await store.update_apply_transaction("txn", "guard", start=item["path"])
            if mode != "interrupted":
                assert await store.update_apply_transaction(
                    "txn", "guard",
                    outcome={"path": item["path"],
                             "status": "UNKNOWN" if mode == "unknown" else "WRITTEN",
                             "after": None if mode == "unknown" else item["after"]})
        if mode not in {"interrupted", "unknown"}:
            assert await store.update_apply_transaction("txn", "guard")
    finally:
        store.close()
        other.close()
    reopened = open_metadata_store(db)
    try:
        row = await reopened.get_apply_transaction("txn")
        assert row["phase"] == {"completed": "COMPLETED", "partial": "PARTIAL",
                                "interrupted": "WRITE_STARTED", "unknown": "UNKNOWN"}[mode]
        assert not await reopened.claim_apply_transaction({**record, "id": "duplicate"}, digest)
        assert not await reopened.update_apply_transaction("txn", "guard", start="test_app.py")
        assert await reopened.reconcile_filesystem_apply() == (1 if mode == "completed" else 0)
        assert await reopened.reconcile_filesystem_apply() == 0
        recovered = await reopened.get_task(task.id)
        assert recovered.status is (
            TaskStatus.COMPLETED if mode == "completed" else TaskStatus.RUNNING)
        node = await reopened.get_execution_node(record["node_id"])
        if mode == "completed":
            assert node.status is NodeStatus.COMPLETED
            assert node.metadata["output"]["transaction_id"] == "txn"
        else:
            assert node.status is NodeStatus.RUNNING

    finally:
        reopened.close()

@pytest.mark.parametrize("decision", [False, True])
async def test_normal_filesystem_approval_waits_for_exact_human_decision(decision):
    from types import SimpleNamespace

    from Sprout.execution.filesystem_apply import _request_approval
    from Sprout.security.approval import ApprovalManager, ApprovalStatus
    from Sprout.storage.local.memory.operational import MemoryOperationalStore

    store = MemoryOperationalStore()
    manager = ApprovalManager(store)
    actor = Principal("owner", authenticated=True, source="cli")
    task = SimpleNamespace(actor=actor)
    arguments = {"action": "filesystem.apply", "transaction": "txn",
                 "files": [{"path": "new.py", "before": None, "after": "abc"}]}
    identity = {"task_id": "task", "session_id": "session", "source": "cli",
                "resource_scope": "txn", "requested_by": actor.user_id}

    async def confirm(plan, review_text):
        assert plan == arguments and not review_text
        assert not await manager.is_approved("filesystem.apply", arguments, **identity)
        return decision

    experiment = SimpleNamespace(runtime=SimpleNamespace(approvals=manager),
                                 confirm_apply=confirm)
    approval = await _request_approval(experiment, task, arguments, identity)
    assert approval.status is (ApprovalStatus.APPROVED if decision else ApprovalStatus.REJECTED)
    assert not await manager.is_approved(
        "filesystem.apply", {**arguments, "transaction": "other"}, **identity)
    assert not await manager.is_approved(
        "filesystem.apply", arguments, **{**identity, "session_id": "other"})
    assert await manager.is_approved("filesystem.apply", arguments, **identity) is decision
    assert not await manager.is_approved("filesystem.apply", arguments, **identity)
