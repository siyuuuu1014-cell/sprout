import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.coding_backend import DockerCodingBackend
from Sprout.execution.models import ProcessResult
from Sprout.orchestration.models import ExecutionNode, NodeStatus
from Sprout.tests.test_coding_container import fixture


def backend(tmp_path):
    request = fixture(tmp_path)
    node = ExecutionNode(id="node", task_id=request.task_id, status=NodeStatus.RUNNING,
                         metadata={"execution_guard": {"state": "active", "owner": "owner"}})
    metadata = SimpleNamespace(get_execution_node=AsyncMock(return_value=node),
                               get_coding_execution=AsyncMock(return_value=None),
                               ensure_coding_intent=AsyncMock(),
                               claim_coding_execution=AsyncMock(return_value=True),
                               finish_coding_execution=AsyncMock(return_value=True))
    inspection = ProcessResult(("docker",), exit_code=0, stdout=json.dumps([
        {"Os": "linux", "Config": {"Labels": {"sprout.role": "isolated-coding",
                                               "sprout.coding.schema": "1"}}}]))
    runner = SimpleNamespace(authorize_task_process=AsyncMock(
        return_value=ProcessResult(("docker",), allowed=True)),
        run_task_process=AsyncMock(side_effect=[
        inspection, ProcessResult(("docker",), exit_code=0)]))
    return request, metadata, runner, DockerCodingBackend(
        metadata, runner, node_id="node", owner="owner")


async def test_live_owner_runs_once_and_rechecks(tmp_path):
    request, metadata, runner, executor = backend(tmp_path)
    assert (await executor.execute(request, node_id="node")).exit_code == 0
    assert metadata.get_execution_node.await_count == 2
    with pytest.raises(ValueError, match="consumed"):
        await executor.execute(request, node_id="node")
    assert runner.run_task_process.await_count == 2


async def test_wrong_owner_blocks_before_image_io(tmp_path):
    request, metadata, runner, executor = backend(tmp_path)
    metadata.get_execution_node.return_value = ExecutionNode(
        task_id=request.task_id, status=NodeStatus.RUNNING,
        metadata={"execution_guard": {"state": "active", "owner": "other"}})
    with pytest.raises(ValueError, match="ownership"):
        await executor.execute(request, node_id="node")
    runner.run_task_process.assert_not_called()


async def test_unknown_result_consumes_attempt(tmp_path):
    request, _, runner, executor = backend(tmp_path)
    inspection = runner.run_task_process.side_effect
    runner.run_task_process.side_effect = [
        next(inspection), ProcessResult(("docker",), error="timeout")]
    with pytest.raises(RuntimeError, match="unconfirmed"):
        await executor.execute(request, node_id="node")
    with pytest.raises(ValueError, match="consumed"):
        await executor.execute(request, node_id="node")


async def test_persisted_attempt_blocks_before_any_process(tmp_path):
    request, metadata, runner, executor = backend(tmp_path)
    metadata.get_coding_execution.return_value = {"state": "uncertain"}
    with pytest.raises(RuntimeError, match="Persisted"):
        await executor.execute(request, node_id="node")
    runner.run_task_process.assert_not_called()


async def test_failed_claim_never_launches_coding(tmp_path):
    request, metadata, runner, executor = backend(tmp_path)
    metadata.claim_coding_execution.return_value = False
    with pytest.raises(RuntimeError, match="claim changed"):
        await executor.execute(request, node_id="node")
    assert runner.run_task_process.await_count == 1  # image inspection only


async def test_unconfirmed_settlement_does_not_return_success(tmp_path):
    request, metadata, runner, executor = backend(tmp_path)
    metadata.finish_coding_execution.return_value = False
    with pytest.raises(RuntimeError, match="settlement"):
        await executor.execute(request, node_id="node")
    assert runner.run_task_process.await_count == 2


async def test_pending_command_approval_does_not_claim_or_launch(tmp_path):
    request, metadata, runner, executor = backend(tmp_path)
    runner.authorize_task_process.return_value = ProcessResult(
        ("docker",), allowed=False, approval_id="pending")
    result = await executor.execute(request, node_id="node")
    assert result.approval_id == "pending"
    metadata.claim_coding_execution.assert_not_called()
    assert runner.run_task_process.await_count == 1  # read-only image inspection


async def test_denied_at_actual_launch_records_no_start_only_with_broker_proof(tmp_path):
    request, metadata, runner, executor = backend(tmp_path)
    inspection = next(runner.run_task_process.side_effect)
    runner.run_task_process.side_effect = [inspection, ProcessResult(
        ("docker",), allowed=False, started=False, approval_id="pending")]
    result = await executor.execute(request, node_id="node")
    assert result.needs_approval
    assert metadata.finish_coding_execution.call_args.args[2] == "not_started"


async def test_legacy_denied_result_cannot_prove_no_launch(tmp_path):
    request, metadata, runner, executor = backend(tmp_path)
    inspection = next(runner.run_task_process.side_effect)
    runner.run_task_process.side_effect = [inspection, ProcessResult(("docker",), allowed=False)]
    with pytest.raises(RuntimeError, match="no launch"):
        await executor.execute(request, node_id="node")
    assert metadata.finish_coding_execution.call_args.args[2] == "uncertain"


async def test_no_launch_retry_rechecks_authorization_before_reclaim(tmp_path):
    request, metadata, runner, executor = backend(tmp_path)
    inspection = next(runner.run_task_process.side_effect)
    runner.run_task_process.side_effect = [inspection, ProcessResult(
        ("docker",), allowed=False, started=False, approval_id="pending")]
    await executor.execute(request, node_id="node")
    record = metadata.claim_coding_execution.call_args.args[0]
    metadata.get_coding_execution.return_value = {**record, "state": "not_started"}
    runner.run_task_process.side_effect = [inspection]
    runner.authorize_task_process.return_value = ProcessResult(
        ("docker",), allowed=False, started=False, approval_id="pending")
    retry = DockerCodingBackend(metadata, runner, node_id="node", owner="owner")
    assert (await retry.execute(request, node_id="node")).needs_approval
    assert metadata.claim_coding_execution.await_count == 1
    assert runner.authorize_task_process.await_count == 2
