from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.coding_import import import_artifacts
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.models import FileResult, SandboxRef
from Sprout.execution.verification_run import input_manifest
from Sprout.orchestration.models import ExecutionNode
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.task.models import Task


def fixture(tmp_path):
    source, target = tmp_path / "export", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for root in (source, target):
        (root / "app.py").write_text("old\n", encoding="utf-8", newline="")
        (root / "remove.py").write_text("old", encoding="utf-8")
    baseline = input_manifest(source)
    (source / "app.py").write_text("new\n", encoding="utf-8", newline="")
    (source / "remove.py").unlink()
    metadata = SimpleNamespace(get_coding_execution=AsyncMock(return_value=None),
                               ensure_coding_intent=AsyncMock(),
                               claim_coding_execution=AsyncMock(return_value=True),
                               finish_coding_execution=AsyncMock(return_value=True))
    policy = SimpleNamespace(decide=lambda request: PolicyDecision(
        AccessDecision.ALLOW if request.action == ActionType.FILE_DELETE
        else AccessDecision.SANDBOX_ONLY, "fixture"))
    broker = FileBroker(policy)
    task = Task(source="cli")
    node = ExecutionNode(task_id=task.id, metadata={"execution_guard": {"owner": "owner"}})
    kwargs = dict(exported=source, baseline=baseline, oracle_paths=("tests",),
                  sandbox=SandboxRef("sandbox", "fixture", target), task=task,
                  node=node, command_slot="import-0")
    return metadata, broker, kwargs


async def test_real_file_broker_import_preserves_bytes_and_requires_delete_preflight(tmp_path):
    metadata, broker, kwargs = fixture(tmp_path)
    output = await import_artifacts(metadata, broker, **kwargs)
    assert output["applied"] and not output["validation_committed"]
    assert (kwargs["sandbox"].root / "app.py").read_bytes() == b"new\n"
    assert not (kwargs["sandbox"].root / "remove.py").exists()
    assert metadata.finish_coding_execution.call_args.args[2] == "completed"


async def test_delete_waits_before_any_write_or_claim(tmp_path):
    metadata, broker, kwargs = fixture(tmp_path)
    broker.authorize_delete = AsyncMock(return_value=FileResult("remove.py", False,
                                                              approval_id="approval"))
    output = await import_artifacts(metadata, broker, **kwargs)
    assert output["approval_id"] == "approval" and output["waiting"]
    assert (kwargs["sandbox"].root / "app.py").read_bytes() == b"old\n"
    metadata.claim_coding_execution.assert_not_called()


async def test_partial_import_remains_uncertain(tmp_path):
    metadata, broker, kwargs = fixture(tmp_path)
    broker.delete = AsyncMock(side_effect=OSError("interrupted"))
    with pytest.raises(OSError):
        await import_artifacts(metadata, broker, **kwargs)
    assert (kwargs["sandbox"].root / "app.py").read_bytes() == b"new\n"
    args = metadata.finish_coding_execution.call_args.args
    assert args[2] == "uncertain" and args[3]["changed_paths"] == ["app.py"]
    metadata.get_coding_execution.return_value = {"state": "uncertain"}
    with pytest.raises(RuntimeError, match="already occupied"):
        await import_artifacts(metadata, broker, **kwargs)


async def test_target_drift_blocks_before_claim(tmp_path):
    metadata, broker, kwargs = fixture(tmp_path)
    (kwargs["sandbox"].root / "app.py").write_text("external change", encoding="utf-8")
    with pytest.raises(ValueError, match="Target changed"):
        await import_artifacts(metadata, broker, **kwargs)
    metadata.claim_coding_execution.assert_not_called()


async def test_settlement_failure_never_returns_success(tmp_path):
    metadata, broker, kwargs = fixture(tmp_path)
    metadata.finish_coding_execution.return_value = False
    with pytest.raises(RuntimeError, match="settlement"):
        await import_artifacts(metadata, broker, **kwargs)
    assert metadata.finish_coding_execution.call_args.args[2] == "uncertain"


async def test_binary_import_rejected_before_claim(tmp_path):
    metadata, broker, kwargs = fixture(tmp_path)
    (kwargs["exported"] / "app.py").write_bytes(b"binary\x00body")
    with pytest.raises(ValueError, match="binary"):
        await import_artifacts(metadata, broker, **kwargs)
    metadata.claim_coding_execution.assert_not_called()
