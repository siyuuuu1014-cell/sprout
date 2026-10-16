from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.coding_session import CodingNodeSession
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.models import ProcessResult, SandboxRef
from Sprout.orchestration.models import ExecutionNode
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus


async def fixture(tmp_path):
    original, target, evidence = (tmp_path / name for name in ("original", "target", "evidence"))
    for root in (original, target, evidence):
        root.mkdir()
    (target / "app.py").write_text("old\n", encoding="utf-8", newline="")
    (target / "tests").mkdir()
    (target / "tests/test_oracle.py").write_text("hidden", encoding="utf-8")
    store = open_metadata_store(str(evidence / "metadata.db"))
    task = Task(status=TaskStatus.RUNNING, metadata={"execution_mode": "code"})
    await store.save_task(task)
    node = ExecutionNode(task_id=task.id)
    await store.save_execution_node(node)
    assert await store.claim_execution_node(node, "owner", "")
    node = await store.get_execution_node(node.id)
    policy = SimpleNamespace(decide=lambda request: PolicyDecision(
        AccessDecision.SANDBOX_ONLY, "fixture"))
    session = CodingNodeSession(store, AsyncMock(), FileBroker(policy), evidence=evidence,
                                original=original, image="sha256:" + "a" * 64,
                                oracle_paths=("tests",),
                                sandbox=SandboxRef("sandbox", "fixture", target),
                                task=task, node=node)
    return store, session


async def test_real_storage_export_dispatch_import_chain(tmp_path, monkeypatch):
    store, session = await fixture(tmp_path)
    async def execute(self, request, *, node_id):
        assert not (request.exported / "tests").exists()
        (request.exported / "app.py").write_text("new\n", encoding="utf-8", newline="")
        return ProcessResult(request.command, exit_code=0, started=True)
    monkeypatch.setattr("Sprout.execution.coding_dispatch.DockerCodingBackend.execute", execute)
    try:
        output = await session.execute_command(("python",), cwd=session.sandbox.root,
                                               command_slot="evaluation-0")
        assert output["imported"] is True
        assert (session.sandbox.root / "app.py").read_bytes() == b"new\n"
        assert (session.sandbox.root / "tests/test_oracle.py").read_text() == "hidden"
    finally:
        store.close()


async def test_partial_export_not_replaced(tmp_path):
    store, session = await fixture(tmp_path)
    try:
        context, _ = await session.prepare()
        (context.evidence / "coding-baseline.json").unlink()
        with pytest.raises(ValueError, match="Incomplete"):
            await session.prepare()
        assert (context.exported / "app.py").read_bytes() == b"old\n"
    finally:
        store.close()


async def test_source_drift_cannot_replace_node_baseline(tmp_path):
    store, session = await fixture(tmp_path)
    try:
        await session.prepare()
        (session.sandbox.root / "app.py").write_text("changed", encoding="utf-8")
        with pytest.raises(ValueError, match="intent changed"):
            await session.prepare()
    finally:
        store.close()


async def test_failed_command_never_imports(tmp_path, monkeypatch):
    store, session = await fixture(tmp_path)
    execute = AsyncMock(return_value=ProcessResult(("docker",), exit_code=1, started=True))
    monkeypatch.setattr("Sprout.execution.coding_dispatch.DockerCodingBackend.execute", execute)
    try:
        output = await session.execute_command(("python",), cwd=session.sandbox.root,
                                               command_slot="evaluation-0")
        assert output["imported"] is False
        assert (session.sandbox.root / "app.py").read_bytes() == b"old\n"
    finally:
        store.close()


async def test_verifier_receives_frozen_session_context(tmp_path, monkeypatch):
    store, session = await fixture(tmp_path)
    verify = AsyncMock(return_value={"verified": True, "validation_committed": False})
    monkeypatch.setattr("Sprout.execution.coding_session.verify_isolated_cases", verify)
    acceptance = {"cases": [{"id": "ok", "target": "app:run", "expected_output": 1}]}
    try:
        output = await session.verify(acceptance_spec=acceptance, regression_spec=acceptance)
        assert output["verified"] and not output["validation_committed"]
        assert verify.call_args.kwargs["node"].id == session.node.id
        context = verify.call_args.kwargs["context"]
        assert context.image == session.image
        assert not (context.exported / "tests").exists()
    finally:
        store.close()
