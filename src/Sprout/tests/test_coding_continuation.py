from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.cli import coding_continuation as module
from Sprout.evolution import candidate_binding
from Sprout.gateway.local_identity import current_cli_principal
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.security.approval import ApprovalRecord, ApprovalStatus
from Sprout.task.models import TaskResult, TaskStatus


def setup(monkeypatch, tmp_path):
    principal = current_cli_principal()
    task = SimpleNamespace(id="experiment", workspace_id="workspace", status="waiting_approval")
    grant = ApprovalRecord(id="approval", tool="isolated_coding_command", task_id=task.id,
        arguments_fingerprint="exact", source="cli", requested_by=principal.user_id,
        resource_scope=task.workspace_id, action_summary='{"command": ["python", "edit.py"]}')
    node = ExecutionNode(id="agent", task_id=task.id, type=NodeType.AGENT,
        status=NodeStatus.WAITING, metadata={"output": {"pending_approvals": [grant.id]}})
    metadata = SimpleNamespace(list_execution_nodes=AsyncMock(return_value=[node]))

    async def decide(*args, **kwargs):
        grant.status = ApprovalStatus.APPROVED
        return grant

    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=metadata),
        approvals=SimpleNamespace(store=SimpleNamespace(get_approval=AsyncMock(return_value=grant)),
                                  decide=AsyncMock(side_effect=decide)),
        pending_approvals=AsyncMock(return_value=[grant]))

    @asynccontextmanager
    async def assembled(*args, **kwargs):
        yield runtime, task

    run = AsyncMock(return_value=TaskResult(task_id=task.id, status=TaskStatus.WAITING_APPROVAL))
    serving = Mock()

    @asynccontextmanager
    async def execution(*args, **kwargs):
        serving(*args, **kwargs)
        yield run

    monkeypatch.setattr(module, "_regular", lambda path: path)
    monkeypatch.setattr(module, "_route", lambda *args: ("original-host", "original-ns", "queue"))
    monkeypatch.setattr(module, "experiment_runtime", assembled)
    monkeypatch.setattr(module, "build_coding_session_factory", Mock())
    monkeypatch.setattr(module, "coding_execution", execution)
    return runtime, task, grant, node, run, serving, dict(settings=None, principal=principal)


async def test_list_wait_does_not_decide_or_execute(monkeypatch, tmp_path):
    runtime, task, grant, _, run, serving, options = setup(monkeypatch, tmp_path)
    result = await module.continue_coding(tmp_path / "metadata.db", task.id, **options)
    assert result["pending_approvals"][0]["approval_id"] == grant.id
    assert "edit.py" in result["pending_approvals"][0]["action_summary"]
    runtime.approvals.decide.assert_not_awaited()
    run.assert_not_awaited()
    serving.assert_not_called()


async def test_explicit_approval_resumes_same_task_and_route(monkeypatch, tmp_path):
    runtime, task, grant, _, run, serving, options = setup(monkeypatch, tmp_path)
    result = await module.continue_coding(tmp_path / "metadata.db", task.id,
                                          approval_id=grant.id, **options)
    assert result["task_id"] == task.id and result["git_executed"] is False
    runtime.approvals.decide.assert_awaited_once()
    run.assert_awaited_once()
    serving.assert_called_once_with(runtime, task, host="original-host",
                                   namespace="original-ns", resume=True)


@pytest.mark.parametrize("change", ["foreign_task", "orphan_id", "wrong_tool", "expired",
                                    "wrong_scope", "wrong_actor"])
async def test_wrong_approval_never_decides_or_runs(monkeypatch, tmp_path, change):
    runtime, task, grant, _, run, serving, options = setup(monkeypatch, tmp_path)
    if change == "foreign_task":
        grant.task_id = "another-task"
    elif change == "orphan_id":
        grant.id = "orphan"
    elif change == "wrong_tool":
        grant.tool = "git_commit"
    elif change == "expired":
        grant.status = ApprovalStatus.EXPIRED
    elif change == "wrong_scope":
        grant.resource_scope = "another-workspace"
    else:
        grant.requested_by = "another-actor"
    with pytest.raises(ValueError, match="Exact current-task"):
        await module.continue_coding(tmp_path / "metadata.db", task.id,
                                     approval_id=grant.id, **options)
    runtime.approvals.decide.assert_not_awaited()
    run.assert_not_awaited()
    serving.assert_not_called()


@pytest.mark.parametrize("status", ["cancelled", "running", "completed", "waiting_resource"])
async def test_other_task_states_are_not_reactivated(monkeypatch, tmp_path, status):
    runtime, task, _, _, run, _, options = setup(monkeypatch, tmp_path)
    task.status = status
    with pytest.raises(ValueError, match="existing approval wait"):
        await module.continue_coding(tmp_path / "metadata.db", task.id, **options)
    runtime.approvals.decide.assert_not_awaited()
    run.assert_not_awaited()


async def test_code_wait_directs_to_commit_without_running(monkeypatch, tmp_path):
    runtime, task, _, _, run, serving, options = setup(monkeypatch, tmp_path)
    runtime.storage.metadata.list_execution_nodes.return_value = [ExecutionNode(
        id="approval", type=NodeType.APPROVAL, status=NodeStatus.WAITING,
        metadata={"output": {"proposal_id": "proposal"}})]
    result = await module.continue_coding(tmp_path / "metadata.db", task.id, **options)
    assert result["proposal_id"] == "proposal" and "commit-*" in result["next_action"]
    with pytest.raises(ValueError, match="independent commit"):
        await module.continue_coding(tmp_path / "metadata.db", task.id, approval_id="a", **options)
    serving.assert_not_called()
    run.assert_not_awaited()


async def test_already_approved_wait_can_continue_without_redeciding(monkeypatch, tmp_path):
    runtime, task, grant, _, run, _, options = setup(monkeypatch, tmp_path)
    grant.status = ApprovalStatus.APPROVED
    runtime.pending_approvals.return_value = []
    await module.continue_coding(tmp_path / "metadata.db", task.id, **options)
    runtime.approvals.decide.assert_not_awaited()
    run.assert_awaited_once()


@pytest.mark.parametrize("changed", [False, True])
async def test_candidate_reconstruction_requires_exact_body(monkeypatch, changed):
    from Sprout.artifacts.models import Artifact
    from Sprout.evolution.isolated_replay import content_digest
    from Sprout.skills.registry import SkillRegistry

    artifact = Artifact(name="procedure", content="original instructions")
    snapshot = {"artifact_id": artifact.id, "artifact_version": artifact.version,
                "content_hash": content_digest("original instructions")}
    if changed:
        from dataclasses import replace

        artifact = replace(artifact, content="changed instructions")
    source = SimpleNamespace(get_artifact=AsyncMock(return_value=artifact))
    runtime = SimpleNamespace(skills=SkillRegistry())
    task = SimpleNamespace(metadata={"replay_plan": {}})
    monkeypatch.setattr(candidate_binding, "verify_plan", lambda plan: {"candidate": snapshot})
    if changed:
        with pytest.raises(ValueError, match="Candidate snapshot changed"):
            await candidate_binding.restore_candidate(runtime, task, source)
        assert runtime.skills.list() == {}
    else:
        await candidate_binding.restore_candidate(runtime, task, source)
        skill = runtime.skills.get(artifact.name)
        assert skill.instructions == artifact.content and skill.trust == "trusted"


@pytest.mark.parametrize("failed", [False, True])
def test_cli_closes_unstarted_source_authority(monkeypatch, tmp_path, failed):
    from typer.testing import CliRunner

    from Sprout.cli.commands.evolution import app

    metadata, settings = object(), object()
    close = AsyncMock()
    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=metadata, close=close))
    monkeypatch.setattr("Sprout.config.loader.load_settings", lambda path=None: settings)
    monkeypatch.setattr("Sprout.runtime.factory.create_runtime", lambda settings: runtime)
    service = AsyncMock(side_effect=ValueError("blocked") if failed else None,
                        return_value={"task_id": "task", "status": "waiting_approval"})
    monkeypatch.setattr(module, "continue_coding", service)
    result = CliRunner().invoke(app, ["replay-continue", "task", "--database",
        str(tmp_path / "metadata.db"), "--approve", "exact-approval"])
    assert result.exit_code == (1 if failed else 0), result.output
    close.assert_awaited_once()
    service.assert_awaited_once()
    assert service.await_args.kwargs["source_metadata"] is metadata
    assert service.await_args.kwargs["approval_id"] == "exact-approval"
