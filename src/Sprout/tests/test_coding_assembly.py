from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.config.loader import default_settings
from Sprout.evolution.coding_assembly import build_coding_session_factory
from Sprout.evolution.replay_config import configuration_contract
from Sprout.evolution.replay_plan import build_plan
from Sprout.evolution.replay_snapshot import runtime_snapshot
from Sprout.gateway.identity import Principal
from Sprout.orchestration.models import ExecutionNode, NodeType
from Sprout.task.models import TaskStatus
from Sprout.tests.test_isolated_git_evidence import fixture as evidence_fixture


async def fixture(tmp_path, monkeypatch):
    store, task, _, sandbox, _, _ = await evidence_fixture(tmp_path)
    principal = Principal(user_id="fixture", source="cli", authenticated=True)
    monkeypatch.setattr("Sprout.evolution.coding_assembly.current_cli_principal", lambda: principal)
    settings = default_settings()
    runtime = SimpleNamespace(_settings=settings, _isolated_coding_required=True,
        storage=SimpleNamespace(metadata=store), tools=SimpleNamespace(list=lambda: {}),
        skills=SimpleNamespace(list=lambda: {}), run_task_process=AsyncMock(),
        authorize_task_process=AsyncMock(), _file_broker=SimpleNamespace(
            write_text=AsyncMock(), delete=AsyncMock(), authorize_delete=AsyncMock()))
    old = task.metadata["replay_plan"]["payload"]
    plan = build_plan(source_task_id=old["source_task_id"], revision=old["revision"], mode="code",
        original=old["original_root"], repo=old["repository_root"], tests=old["test_commands"],
        regressions=old["regression_commands"], oracle_paths=old["oracle_paths"],
        frozen=old["oracle_snapshot"], image=old["verification_image"],
        configuration=configuration_contract(settings), runtime=runtime_snapshot(runtime),
        creator=old["creator"], coding=old["coding_profile"])
    task = replace(task, actor=principal, source="cli",
                   metadata={**task.metadata, "replay_plan": plan},
                   delegation_scope=replace(task.delegation_scope,
                       allowed_actions=frozenset({"file.write", "process.run"})))
    await store.save_task(task)
    node = ExecutionNode(id=task.id + ":evaluation", task_id=task.id, type=NodeType.EVALUATION)
    await store.save_execution_node(node)
    assert await store.claim_execution_node(node, "new-owner", "")
    node = await store.get_execution_node(node.id)
    return store, runtime, task, node, sandbox


async def test_real_plan_bound_session_prepares_without_enabling_runtime(tmp_path, monkeypatch):
    store, runtime, task, node, sandbox = await fixture(tmp_path, monkeypatch)
    try:
        factory = build_coding_session_factory(runtime, task)
        session = factory(task=task, node=node, sandbox=sandbox)
        context, manifest = await session.prepare()
        assert context.image == task.metadata["replay_plan"]["payload"]["coding_profile"]["image"]
        assert context.evidence.parent.name == "coding-evidence"
        assert "app.py" in manifest and session.regression_spec is not None
        assert not hasattr(runtime, "_isolated_coding_dispatch")
        assert not hasattr(runtime, "_isolated_coding_session_factory")
        runtime.run_task_process.assert_not_called()
        runtime.authorize_task_process.assert_not_called()
    finally:
        store.close()


@pytest.mark.parametrize("change", ["task", "scope", "cancelled", "configuration", "identity"])
async def test_live_task_config_or_identity_drift_blocks_prepare(tmp_path, monkeypatch, change):
    store, runtime, task, node, sandbox = await fixture(tmp_path, monkeypatch)
    try:
        session = build_coding_session_factory(runtime, task)(task=task, node=node, sandbox=sandbox)
        if change == "task":
            await store.save_task(replace(task, instruction="changed"))
        elif change == "scope":
            await store.save_task(replace(task, delegation_scope=replace(
                task.delegation_scope, allowed_actions=frozenset({"file.write"}))))
        elif change == "cancelled":
            await store.update_task_status(task.id, TaskStatus.CANCELLED)
        elif change == "configuration":
            runtime._settings.model.model = "changed"
        else:
            monkeypatch.setattr("Sprout.evolution.coding_assembly.current_cli_principal",
                lambda: Principal(user_id="other", source="cli", authenticated=True))
        with pytest.raises(ValueError):
            await session.prepare()
        runtime.run_task_process.assert_not_called()
    finally:
        store.close()


async def test_foreign_task_callback_is_rejected(tmp_path, monkeypatch):
    store, runtime, task, node, sandbox = await fixture(tmp_path, monkeypatch)
    try:
        factory = build_coding_session_factory(runtime, task)
        with pytest.raises(ValueError, match="callback"):
            factory(task=replace(task, id="other"), node=node, sandbox=sandbox)
    finally:
        store.close()
