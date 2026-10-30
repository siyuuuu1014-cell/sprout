"""Versioned ordinary CODE claim on real SQLite, no process effects."""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from Sprout.tests.test_r04_f3_manifest import execution as execution
from Sprout.tests.test_r04_f3_manifest import f3 as f3
from Sprout.tests.test_r04_f3_manifest import rig as rig


def record(e):
    scope = {"version": 1, **asdict(e.rig.task.delegation_scope)}
    scope = json.loads(
        json.dumps(
            scope, default=lambda v: sorted(v) if isinstance(v, (set, frozenset)) else v.isoformat()
        )
    )
    return {
        "schema_version": 3,
        "id": "new-f3-claim",
        "task_id": e.rig.task.id,
        "workspace_id": e.rig.workspace.id,
        "node_id": e.rig.node.id,
        "owner": e.rig.node.metadata["execution_guard"]["owner"],
        "authority": {
            "actor": asdict(e.rig.task.actor),
            "scope": scope,
            "task_metadata": deepcopy(e.rig.task.metadata),
            "node_metadata": deepcopy(e.rig.node.metadata),
            "workspace_root": str(e.rig.workspace.root),
            "workspace_revision": e.rig.workspace.revision,
            "instruction": e.rig.task.instruction,
        },
    }


async def test_v3_claim_once_and_unknown_never_retries(f3):
    e = f3
    store = e.rig.storage.metadata
    value = record(e)
    results = await asyncio.gather(*(store.claim_verification_execution(value) for _ in range(8)))
    assert sum(results) == 1
    assert await store.finish_verification_execution(
        value["id"], value["owner"], "uncertain", {"state": "UNKNOWN"}
    )
    assert not await store.claim_verification_execution(value)
    e.spawn.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "owner",
        "task",
        "node",
        "workspace",
        "actor",
        "scope",
        "guard",
        "completed",
        "cancelled",
        "waiting_approval",
        "expired",
        "version",
        "v2",
    ],
)
async def test_invalid_claim_has_no_execution_record(f3, change):
    from datetime import UTC, datetime, timedelta

    from Sprout.task.models import TaskStatus

    e = f3
    store = e.rig.storage.metadata
    value = record(e)
    if change in {"owner", "task", "node", "workspace"}:
        key = {"task": "task_id", "node": "node_id", "workspace": "workspace_id"}.get(
            change, change
        )
        value[key] = "wrong"
    if change == "actor":
        value["authority"]["actor"]["authenticated"] = False
    if change == "scope":
        value["authority"]["scope"]["allowed_actions"] = []
    if change == "guard":
        value["authority"]["node_metadata"]["execution_guard"]["state"] = "uncertain"
    if change in {"completed", "cancelled", "waiting_approval"}:
        await store.update_task_status(e.rig.task.id, TaskStatus(change))
    if change == "expired":
        await store.save_task(
            replace(
                e.rig.task,
                delegation_scope=replace(
                    e.rig.task.delegation_scope, expires_at=datetime.now(UTC) - timedelta(seconds=1)
                ),
            )
        )
    if change in {"version", "v2"}:
        value["schema_version"] = 99 if change == "version" else 2
    assert not await store.claim_verification_execution(value)
    assert await store.get_verification_execution(value["id"]) is None
    e.spawn.assert_not_called()


async def test_v2_completed_task_compatibility(f3):
    from Sprout.task.models import TaskStatus

    e = f3
    await e.rig.storage.metadata.update_task_status(e.rig.task.id, TaskStatus.COMPLETED)
    value = {
        "schema_version": 2,
        "id": "old-contract",
        "task_id": e.rig.task.id,
        "owner": "legacy-execution-owner",
    }
    assert await e.rig.storage.metadata.claim_verification_execution(value)
    assert not await e.rig.storage.metadata.claim_verification_execution(record(e))


async def test_v3_real_file_close_reopen_preserves_uncertain_without_retry(f3):
    from types import SimpleNamespace

    from Sprout.orchestration.models import ExecutionNode, NodeBudget, NodeType
    from Sprout.storage.local.sqlite.metadata import open_metadata_store

    e = f3
    path = e.root.parent / "p15-records.db"
    store = open_metadata_store(path)
    await store.save_workspace(e.rig.workspace)
    await store.save_task(e.rig.task)
    node = ExecutionNode(
        id="file-evaluation",
        task_id=e.rig.task.id,
        type=NodeType.EVALUATION,
        budget=NodeBudget(timeout_seconds=300),
    )
    await store.save_execution_node(node)
    assert await store.claim_execution_node(node, "file-owner", "")
    node = await store.get_execution_node(node.id)
    value = record(
        SimpleNamespace(rig=SimpleNamespace(task=e.rig.task, workspace=e.rig.workspace, node=node))
    )
    assert await store.claim_verification_execution(value)
    store.close()
    reopened = open_metadata_store(path)
    try:
        loaded = await reopened.get_verification_execution(value["id"])
        assert loaded["schema_version"] == 3 and loaded["state"] == "active"
        assert await reopened.finish_verification_execution(
            value["id"], "file-owner", "uncertain", {"outcome": "UNKNOWN"}
        )
        assert not await reopened.claim_verification_execution(value)
    finally:
        reopened.close()
