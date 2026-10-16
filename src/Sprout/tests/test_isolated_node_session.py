import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.orchestration.models import ExecutionNode, NodeType
from Sprout.runtime.nodes import NodeExecutor
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import Task


def executor(tmp_path, *, factory=None):
    instance = object.__new__(NodeExecutor)
    sandbox = ExecutionNode(type=NodeType.SANDBOX, metadata={"output": {
        "id": "sandbox", "kind": "fixture", "root": str(tmp_path)}})
    instance._metadata = SimpleNamespace(list_execution_nodes=AsyncMock(return_value=[sandbox]))
    instance._isolated_coding_required = True
    instance._coding_session_factory = factory
    instance._process_broker = SimpleNamespace(run=AsyncMock())
    instance._static_check_commands = AsyncMock(side_effect=AssertionError("No host Git checks"))
    return instance


async def test_missing_session_waits_without_host_checks(tmp_path):
    instance = executor(tmp_path)
    task = Task()
    result = await instance._evaluation(ExecutionNode(task_id=task.id),
                                        SimpleNamespace(root=tmp_path), task)
    assert result["waiting"] and result["reason"] == "isolated_session_not_assembled"
    instance._process_broker.run.assert_not_called()
    instance._static_check_commands.assert_not_called()


@pytest.mark.parametrize("key", ["test_commands", "build_commands", "verification_commands"])
async def test_required_auxiliary_checks_are_not_silently_omitted(tmp_path, key):
    factory = Mock(return_value=SimpleNamespace())
    instance = executor(tmp_path, factory=factory)
    task = Task()
    node = ExecutionNode(task_id=task.id, metadata={key: ["required command"]})
    result = await instance._evaluation(node, SimpleNamespace(root=tmp_path), task)
    assert result["waiting"]
    assert result["reason"] == "isolated_auxiliary_checks_adapter_required"
    factory.assert_called_once()
    instance._process_broker.run.assert_not_called()
    instance._static_check_commands.assert_not_called()


@pytest.mark.parametrize("auxiliary_passed", [None, True, False])
async def test_session_evaluation_reuses_existing_completion_report(tmp_path, auxiliary_passed):
    spec = AcceptanceSpec.from_payload({"cases": [
        {"id": "ok", "target": "app:run", "expected_output": 1}]}).to_payload()
    output = {"status": "passed", "waiting": False, "acceptance_spec": spec,
              "demo_path": str(tmp_path / "public-demo.py"), "test_results": [
                  {"name": "acceptance:ok: isolated-json", "passed": True, "executed": True},
                  {"name": "poc: isolated-public-demo", "passed": True, "executed": True}],
              "results": []}
    session = SimpleNamespace(regression_spec=spec, verify=AsyncMock(return_value=output))
    if auxiliary_passed is not None:
        session.verify_auxiliary = AsyncMock(return_value={
            "waiting": False, "verified": auxiliary_passed,
            "status": "passed" if auxiliary_passed else "failed", "results": [],
            "test_results": [{"name": "build: required", "passed": auxiliary_passed,
                              "executed": True}]})
    factory = Mock(return_value=session)
    instance = executor(tmp_path, factory=factory)
    fingerprint = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    nodes = instance._metadata.list_execution_nodes.return_value
    nodes.append(ExecutionNode(type=NodeType.TEST_DESIGN, metadata={"output": {
        "acceptance_spec": spec, "isolated_design": True,
        "acceptance_fingerprint": fingerprint}}))
    instance._file_broker = SimpleNamespace(read_text=AsyncMock())
    task = Task(metadata={"acceptance_spec": spec})
    node = ExecutionNode(task_id=task.id, metadata=(
        {"build_commands": ["required"]} if auxiliary_passed is not None else {}))
    result = await instance._evaluation(node, SimpleNamespace(root=tmp_path), task)
    assert result["verification_report"]["verified"] is (auxiliary_passed is not False)
    if auxiliary_passed is not None:
        session.verify_auxiliary.assert_awaited_once_with(node.metadata)
    session.verify.assert_awaited_once_with(acceptance_spec=spec, regression_spec=spec,
                                            phase="evaluation")
    instance._process_broker.run.assert_not_called()
    instance._file_broker.read_text.assert_not_called()


async def test_isolated_baseline_does_not_generate_model_tests_or_write_repository(tmp_path):
    spec = {"cases": [{"id": "ok", "target": "app:run", "expected_output": 1}]}
    session = SimpleNamespace(verify=AsyncMock(return_value={"status": "failed", "results": []}))
    instance = executor(tmp_path, factory=Mock(return_value=session))
    instance._file_broker = SimpleNamespace(write_text=AsyncMock())
    instance._plan = AsyncMock()
    task = Task(metadata={"acceptance_spec": spec})
    result = await instance._test_design(ExecutionNode(task_id=task.id), None, task)
    assert result["isolated_design"] and result["baseline_failed"]
    assert "test_path" not in result
    instance._file_broker.write_text.assert_not_called()
    instance._plan.assert_not_called()


async def test_missing_source_acceptance_waits_without_inventing_tests(tmp_path):
    factory = Mock()
    instance = executor(tmp_path, factory=factory)
    instance._plan = AsyncMock()
    result = await instance._test_design(ExecutionNode(), None, Task())
    assert result["reason"] == "frozen_source_acceptance_required"
    factory.assert_not_called()
    instance._plan.assert_not_called()
