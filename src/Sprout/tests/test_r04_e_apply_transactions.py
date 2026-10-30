"""Real SQLite ordinary Apply ledger; no file-write authorization implied."""
import asyncio
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.gateway.identity import Principal
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import TaskStatus
from Sprout.tests.test_git_transaction_store import fixture


async def setup(tmp_path):
    path, store, proposal, legacy, snapshot = await fixture(tmp_path)
    task = await store.get_task(proposal.task_id)
    actor = Principal(user_id="test-owner", source="cli", authenticated=True)
    await store.save_task(replace(task, actor=actor, source="cli",
                                  metadata={"execution_mode": "code"}))
    plan = {"version": 1, "files": [{"path": name, "identity": {
        "device": 1, "inode": i + 1, "size": 2, "mtime_ns": 1, "ctime_ns": 1},
        "before": "a" * 64, "after": "b" * 64} for i, name in enumerate(["a.py", "b.py"])]}
    record = {"version": 1, "id": "ordinary", "task_id": task.id,
              "workspace_id": task.workspace_id, "proposal_id": proposal.id,
              "node_id": legacy["node_id"], "owner": "owner", "target": legacy["target"],
              "principal": asdict(actor), "plan": plan, "plan_sha256": fingerprint(plan),
              "approval_id": "reference-not-authority"}
    return path, store, record, snapshot


async def test_claim_concurrency_and_reopen(tmp_path):
    path, store, record, snapshot = await setup(tmp_path)
    other = open_metadata_store(str(path))
    try:
        results = await asyncio.gather(store.claim_apply_transaction(record, snapshot),
                                      other.claim_apply_transaction(record, snapshot))
        assert sorted(results) == [False, True]
        assert not await store.claim_apply_transaction({**record, "id": "duplicate"}, snapshot)
        assert await store.update_apply_transaction("ordinary", "owner", start="a.py")
        assert (await store.get_apply_transaction("ordinary"))["phase"] == "WRITE_STARTED"
    finally:
        store.close()
        other.close()
    store = open_metadata_store(str(path))
    try:
        row = await store.get_apply_transaction("ordinary")
        assert row["files"]["a.py"]["status"] == "WRITE_STARTED"
        assert not await store.update_apply_transaction("ordinary", "owner", start="a.py")
        assert not await store.update_apply_transaction("ordinary", "owner", start="b.py")
        assert await store.update_apply_transaction("ordinary", "owner", reason="interrupted")
        assert (await store.get_apply_transaction("ordinary"))["phase"] == "UNKNOWN"
        assert not await store.update_apply_transaction("ordinary", "owner", start="b.py")
        assert not await store.finish_git_transaction("ordinary", "owner", "uncertain", {})
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["complete", "partial", "failed", "unknown"])
async def test_durable_settlement(tmp_path, mode):
    path, store, record, snapshot = await setup(tmp_path)
    assert await store.claim_apply_transaction(record, snapshot)
    try:
        if mode != "failed":
            for name in (["a.py", "b.py"] if mode == "complete" else ["a.py"]):
                assert await store.update_apply_transaction("ordinary", "owner", start=name)
                status = "UNKNOWN" if mode == "unknown" else "WRITTEN"
                assert await store.update_apply_transaction("ordinary", "owner", outcome={
                    "path": name, "status": status,
                    "after": None if status == "UNKNOWN" else "b" * 64})
        if mode != "unknown":
            assert await store.update_apply_transaction("ordinary", "owner", reason=mode)
        row = await store.get_apply_transaction("ordinary")
        assert row["phase"] == {"complete": "COMPLETED", "partial": "PARTIAL",
                                "failed": "FAILED", "unknown": "UNKNOWN"}[mode]
        assert not await store.update_apply_transaction("ordinary", "owner", reason="again")
    finally:
        store.close()
    store = open_metadata_store(str(path))
    try:
        assert (await store.get_apply_transaction("ordinary"))["phase"] == row["phase"]
        assert not await store.claim_apply_transaction(record, snapshot)
    finally:
        store.close()


@pytest.mark.parametrize("change", ["owner", "actor", "plan", "cancelled", "snapshot"])
async def test_invalid_claim_no_record(tmp_path, change):
    _, store, record, snapshot = await setup(tmp_path)
    try:
        if change == "owner":
            record["owner"] = "wrong"
        elif change == "actor":
            record["principal"]["user_id"] = "other"
        elif change == "plan":
            record["plan"]["files"][0]["after"] = "c" * 64
        elif change == "cancelled":
            await store.update_task_status(record["task_id"], TaskStatus.CANCELLED)
        else:
            snapshot["risk"] = "changed"
        try:
            result = await store.claim_apply_transaction(record, snapshot)
        except ValueError:
            result = False
        assert not result
        assert await store.get_apply_transaction("ordinary") is None
    finally:
        store.close()


async def test_wrong_owner_cancelled_start_and_false_result(tmp_path):
    _, store, record, snapshot = await setup(tmp_path)
    try:
        assert await store.claim_apply_transaction(record, snapshot)
        assert not await store.update_apply_transaction("ordinary", "wrong", start="a.py")
        assert await store.update_apply_transaction("ordinary", "owner", start="a.py")
        with pytest.raises(ValueError, match="digest"):
            await store.update_apply_transaction("ordinary", "owner", outcome={
                "path": "a.py", "status": "WRITTEN", "after": "c" * 64})
        assert await store.update_apply_transaction("ordinary", "owner", outcome={
            "path": "a.py", "status": "REJECTED", "after": "a" * 64})
        await store.update_task_status(record["task_id"], TaskStatus.CANCELLED)
        assert not await store.update_apply_transaction("ordinary", "owner", start="b.py")
        assert await store.update_apply_transaction("ordinary", "owner", reason="cancelled")
        assert (await store.get_apply_transaction("ordinary"))["phase"] == "FAILED"
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["written", "partial", "unknown"])
async def test_native_broker_with_durable_start(tmp_path, mode):
    import hashlib
    import os
    from unittest.mock import Mock

    from Sprout.execution.file_broker import FileBroker
    from Sprout.execution.file_snapshot import ResourceIdentity
    from Sprout.security.access import AccessDecision, ActionRequest, ActionType, PolicyDecision
    from Sprout.security.engine import PolicyEngine
    from Sprout.task.models import DelegationScope
    from Sprout.workspace.classifier import classify
    from Sprout.workspace.models import ResourceRef, Workspace

    if os.name != "nt":
        pytest.skip("Windows native write integration only")
    _, store, record, snapshot = await setup(tmp_path)
    root = Path(record["target"])
    root.mkdir()
    for item in record["plan"]["files"]:
        path = root / item["path"]
        path.write_bytes(b"old")
        item.update(identity=asdict(ResourceIdentity.from_stat(path.stat())),
                    before=hashlib.sha256(b"old").hexdigest(),
                    after=hashlib.sha256(b"new").hexdigest())
    record["plan_sha256"] = fingerprint(record["plan"])
    assert await store.claim_apply_transaction(record, snapshot)
    actor = Principal(**record["principal"])
    workspace = Workspace(id=record["workspace_id"], root=root)
    policy = PolicyEngine()
    policy.decide = Mock(return_value=PolicyDecision(AccessDecision.ALLOW))
    broker = FileBroker(policy)
    token = object()

    async def resolve(permit):
        row = await store.get_apply_transaction("ordinary")
        if (permit is not token or row["physical_state"] != "active"
                or row["files"].get("a.py", {}).get("status") != "WRITE_STARTED"):
            raise PermissionError("durable_start_required")
        item = record["plan"]["files"][0]
        target = root / item["path"]
        request = ActionRequest(task_id=record["task_id"], actor=actor,
            action=ActionType.FILE_WRITE, scope=DelegationScope(
                allowed_paths=(target.as_posix(),), allowed_actions=frozenset({"file.write"})),
            resource=ResourceRef(workspace.id, target.as_posix(),
                                 classify(target, workspace_root=root)))
        return (request, workspace, item["path"], ResourceIdentity(**item["identity"]),
                item["before"], b"new")

    # Stable ActionRequest identity across refresh; resolver is controlled Host
    # admission, NOT a production Runtime/approval receipt.
    original = resolve
    stable = None

    async def bound(permit):
        nonlocal stable
        current = await original(permit)
        if stable is None:
            stable = current
        return stable

    broker._apply_resolver = bound
    try:
        with pytest.raises(PermissionError, match="durable_start"):
            await broker._conditional_apply_write(token)
        assert (root / "a.py").read_bytes() == b"old"
        assert await store.update_apply_transaction("ordinary", "owner", start="a.py")
        result = await broker._conditional_apply_write(token)
        assert (root / "a.py").read_bytes() == b"new"
        assert await store.update_apply_transaction("ordinary", "owner", outcome={
            "path": "a.py", "status": "UNKNOWN" if mode == "unknown" else "WRITTEN",
            "after": None if mode == "unknown" else result["sha256"]})
        if mode == "written":
            # This test intentionally writes one of two planned files; PARTIAL
            # is required. Full transaction completion is covered above.
            assert await store.update_apply_transaction("ordinary", "owner", reason="stopped")
        elif mode == "partial":
            assert await store.update_apply_transaction(
                "ordinary", "owner", reason="second refused")
        row = await store.get_apply_transaction("ordinary")
        assert row["phase"] == ("UNKNOWN" if mode == "unknown" else "PARTIAL")
        assert (root / "b.py").read_bytes() == b"old"
        assert not await store.update_apply_transaction("ordinary", "owner", start="a.py")
    finally:
        store.close()
