import os
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest

from Sprout.execution import foreground_git_apply
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.models import SandboxRef
from Sprout.execution.node_result_source import payload_digest
from Sprout.gateway.identity import Principal
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.storage.local.memory.operational import MemoryOperationalStore
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import DelegationScope, Task, TaskStatus
from Sprout.workspace.models import Workspace, WorkspaceKind


@pytest.mark.skipif(os.name != "nt", reason="Windows FileBroker conditional write")
@pytest.mark.parametrize("outcome", ["passed", "retest_failed", "write_refused", "external_change"])
async def test_git_apply_native_file_and_ledger(tmp_path, monkeypatch, outcome):
    root = tmp_path / "project"
    root.mkdir()
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    before_data, after_data = b"original", b"reviewed"
    before, after = (sha256(value).hexdigest() for value in (before_data, after_data))
    target = root / "app.py"
    target.write_bytes(before_data)
    (root / "unrelated.py").write_bytes(b"preserve")
    (candidate_root / "app.py").write_bytes(after_data)
    workspace = Workspace("workspace", root, kind=WorkspaceKind.GIT_REPOSITORY)
    actor = Principal("owner", authenticated=True, source="cli")
    bundle = {"source_files": {"app.py": before}, "bundle_sha256": "a" * 64}
    revision = "b" * 40
    task = Task(
        workspace_id=workspace.id, actor=actor, source="cli", status=TaskStatus.RUNNING,
        delegation_scope=DelegationScope(
            allowed_paths=("app.py",), allowed_actions=frozenset(
                {"file.read", "file.write", "file.apply"}),
        ),
        metadata={"session_id": "session", "git_acceptance_v1": bundle,
                  "coding_preparation_v1": {"writes": ["app.py"]},
                  "host_acceptance_v2": {"bundle_sha256": bundle["bundle_sha256"],
                                         "repository": {"revision": revision}}},
    )
    evaluation = {"verified": True}
    review = {"status": "reviewed", "files_changed": ["app.py"], "diff": "reviewed patch",
              "bundle_sha256": bundle["bundle_sha256"], "git_baseline": revision,
              "evaluation_node_id": f"{task.id}:evaluation",
              "verification_sha256": payload_digest(evaluation),
              "input_manifest": {"app.py": after}}
    ref = SandboxRef("sandbox", "git_worktree", candidate_root)
    manager = ApprovalManager(MemoryOperationalStore())
    broker = FileBroker(PolicyEngine())
    permits = {}

    async def resolve(permit):
        return await permits[permit]()

    broker._apply_resolver = resolve
    runtime = SimpleNamespace(
        approvals=manager, _file_broker=broker, _policy_engine=PolicyEngine(),
        _filesystem_apply_permits=permits,
        _git_broker=SimpleNamespace(_authorize_review=AsyncMock()),
    )
    store = open_metadata_store(str(tmp_path / "metadata.db"))
    try:
        await store.save_workspace(workspace)
        await store.save_task(task)
        for kind, output in ((NodeType.EVALUATION, evaluation), (NodeType.APPROVAL, review),
                             (NodeType.APPLY, None)):
            node = ExecutionNode(id=f"{task.id}:{kind.value}", task_id=task.id, type=kind)
            await store.save_execution_node(node)
            assert await store.claim_execution_node(node, "guard", "")
            active = await store.get_execution_node(node.id)
            if output is not None:
                settled = replace(active, status=NodeStatus.COMPLETED,
                                  metadata={**active.metadata, "output": output})
                assert await store.finish_execution_node(settled, "guard", "completed")

        frame = SimpleNamespace(guard_owner="guard")
        monkeypatch.setattr(foreground_git_apply, "_current_project_node", lambda: frame)
        monkeypatch.setattr(foreground_git_apply, "report", lambda *args: None)
        assembly = SimpleNamespace(
            runtime=runtime, workspace=workspace, store=store, subject=AsyncMock(return_value=task),
            restore_review=AsyncMock(return_value=(ref, review)), sandbox=ref, bundle=bundle,
            check_binding=lambda **_: None, automatic=True,
            provider=SimpleNamespace(_check_git_root=lambda _: None,
                                     review_patch=AsyncMock(return_value=review["diff"])),
        )

        async def retest(*args):
            assert target.read_bytes() == after_data
            return {"verified": outcome == "passed", "input_manifest": review["input_manifest"],
                    "bundle_sha256": bundle["bundle_sha256"]}

        monkeypatch.setattr(foreground_git_apply, "retest", retest)
        if outcome == "external_change":
            target.write_bytes(b"outside edit")
        if outcome == "write_refused":
            from Sprout.execution.file_access import ConditionalWriteRefused

            monkeypatch.setattr(broker, "_conditional_apply_write", AsyncMock(
                side_effect=ConditionalWriteRefused("native write refused")))
        result = await foreground_git_apply.execute(assembly, active)
        transaction = str(uuid5(NAMESPACE_URL, "foreground-git-apply:" + task.id))
        row = await store.get_apply_transaction(transaction)
        assert (root / "unrelated.py").read_bytes() == b"preserve"
        if outcome == "external_change":
            assert row is None
            assert result["error"] == "foreground_git_apply_target_conflict:app.py"
            assert target.read_bytes() == b"outside edit"
        else:
            assert result["transaction_id"] == transaction
            assert row["phase"] == {"passed": "COMPLETED", "retest_failed": "ROLLED_BACK",
                                    "write_refused": "FAILED"}[outcome]
            assert target.read_bytes() == (after_data if outcome == "passed" else before_data)
            if outcome == "passed":
                assert result["applied"]
                assert (await foreground_git_apply.execute(assembly, active))["reused"]
                assert await store.reconcile_filesystem_apply() == 1
                assert (await store.get_task(task.id)).status is TaskStatus.COMPLETED
            else:
                assert result["git_apply_failed"]
    finally:
        store.close()


@pytest.mark.parametrize("case", ["ready", "legacy", "completed", "uncertain"])
async def test_runtime_git_resume_uses_only_authorized_review_entry(case):
    from contextlib import asynccontextmanager

    from Sprout.runtime.runtime import Runtime

    actor = Principal("owner", authenticated=True, source="cli")
    task = Task(
        workspace_id="workspace", actor=actor, source="cli",
        status=TaskStatus.COMPLETED if case == "completed" else TaskStatus.WAITING_RESOURCE,
        delegation_scope=DelegationScope(
            allowed_actions=frozenset({"file.apply"} if case != "legacy" else {"file.write"}),
        ),
        metadata={"session_id": "session", "host_acceptance_v2": {},
                  "coding_preparation_v1": {"writes": ["app.py"]}},
    )

    @asynccontextmanager
    async def lock(_):
        yield

    execute = AsyncMock(return_value="apply")
    store = SimpleNamespace(
        reconcile_filesystem_apply=AsyncMock(), get_task=AsyncMock(return_value=task),
        get_apply_transaction=AsyncMock(return_value={} if case == "uncertain" else None),
    )
    runtime = SimpleNamespace(
        _workspace_locks=SimpleNamespace(lock=lock), _metadata_or_raise=lambda: store,
        _sessions=SimpleNamespace(require_owner=AsyncMock(), frozen_principal=lambda _: actor),
        session_workspace_id=lambda _: "workspace", _execute_task=execute,
        _cli_code_writes={task.id: (actor, "session", "workspace", ("app.py",))},
    )
    if case in {"legacy", "uncertain"}:
        with pytest.raises(PermissionError, match="scope_missing|reconciliation_required"):
            await Runtime._resume_foreground_git(runtime, task)
        execute.assert_not_awaited()
    elif case == "completed":
        assert (await Runtime._resume_foreground_git(runtime, task)).status is TaskStatus.COMPLETED
        execute.assert_not_awaited()
    else:
        assert await Runtime._resume_foreground_git(runtime, task) == "apply"
        execute.assert_awaited_once_with(task, artifact_snapshot=None, foreground_git_resume=True)
