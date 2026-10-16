from unittest.mock import AsyncMock

from Sprout.execution.isolated_acceptance import verify_isolated_cases
from Sprout.execution.models import ProcessResult
from Sprout.orchestration.models import ExecutionNode
from Sprout.tests.test_coding_container import fixture


def spec():
    return {"cases": [{"id": "ok", "target": "app:run", "args": [2],
                       "expected_output": "hidden-expected"}]}


async def test_host_judges_acceptance_regression_and_actual_demo(tmp_path, monkeypatch):
    context = fixture(tmp_path)
    node = ExecutionNode(task_id=context.task_id, metadata={"execution_guard": {"owner": "owner"}})
    observed = []

    async def execute(self, request, *, node_id):
        assert node_id == node.id and request.read_only_input is True
        assert "hidden-expected" not in str(request.command)
        observed.append(request.command)
        return ProcessResult(request.command, exit_code=0, stdout='"hidden-expected"')
    monkeypatch.setattr("Sprout.execution.isolated_acceptance.DockerCodingBackend.execute", execute)
    output = await verify_isolated_cases(AsyncMock(), AsyncMock(), context=context, node=node,
                                         acceptance_spec=spec(), regression_spec=spec())
    assert output["verified"] and not output["validation_committed"]
    assert len(observed) == 3
    assert [item["kind"] for item in output["results"]] == ["acceptance", "regression", "poc"]
    assert output["test_results"][-1]["name"].startswith("poc: ")
    demo = context.evidence / "evaluation-public-demo.py"
    assert "hidden-expected" not in demo.read_text(encoding="utf-8")
    assert observed[-1][3] == demo.read_text(encoding="utf-8")


async def test_missing_regression_waits_before_commands(tmp_path, monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr("Sprout.execution.isolated_acceptance.DockerCodingBackend.execute", execute)
    context = fixture(tmp_path)
    node = ExecutionNode(task_id=context.task_id, metadata={"execution_guard": {"owner": "owner"}})
    output = await verify_isolated_cases(AsyncMock(), AsyncMock(), context=context, node=node,
                                         acceptance_spec=spec(), regression_spec=None)
    assert output["waiting"] and not output["verified"]
    execute.assert_not_called()


async def test_fake_pass_verdict_cannot_replace_actual(tmp_path, monkeypatch):
    execute = AsyncMock(return_value=ProcessResult(
        ("docker",), exit_code=0, stdout='{"passed":true}'))
    monkeypatch.setattr("Sprout.execution.isolated_acceptance.DockerCodingBackend.execute", execute)
    context = fixture(tmp_path)
    node = ExecutionNode(task_id=context.task_id, metadata={"execution_guard": {"owner": "owner"}})
    output = await verify_isolated_cases(AsyncMock(), AsyncMock(), context=context, node=node,
                                         acceptance_spec=spec(), regression_spec=spec())
    assert output["status"] == "failed" and not output["verified"]
