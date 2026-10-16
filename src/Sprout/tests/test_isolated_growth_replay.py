"""Replay creates independent tasks without resuming source completed nodes."""

from pathlib import Path

import pytest

from Sprout.artifacts.models import Artifact, ArtifactStatus
from Sprout.evolution.isolated_replay import content_digest, git, prepare
from Sprout.evolution.replay_runner import ReplayRunner
from Sprout.gateway.local_identity import current_cli_principal
from Sprout.runtime.factory import create_runtime
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.tests.test_temporal_integration import _isolated_settings
from Sprout.trajectory.models import ArtifactSnapshot


async def test_missing_configuration_never_executes_original(tmp_path, monkeypatch):
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    runtime = create_runtime(_isolated_settings(tmp_path / "source-runtime"))
    try:
        repo = tmp_path / "source"
        repo.mkdir()
        workspace = await runtime.open_workspace(repo)
        task = await runtime.create_task(workspace.id, "Read only", execution_mode="read_only")
        result = await ReplayRunner(runtime).run(task.id)
        assert result.status == "waiting_resource" and result.task_id == ""
        assert (await runtime.get_task(task.id)).status == "created"
        assert not await runtime.storage.metadata.list_execution_nodes(task.id)
    finally:
        await runtime.stop()


async def test_completed_source_replays_as_new_task_in_fixed_copy(tmp_path, monkeypatch):
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    settings = _isolated_settings(tmp_path / "source-runtime")
    runtime = create_runtime(settings)
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(
        repo,
        "-c",
        "user.name=Replay Fixture",
        "-c",
        "user.email=replay@invalid.example",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "baseline",
    )
    revision = git(repo, "rev-parse", "HEAD")
    await runtime.start()
    try:
        workspace = await runtime.open_workspace(repo)
        task = await runtime.create_task(workspace.id, "Explain app.py", execution_mode="read_only")
        assert (await runtime.execute(task)).status == "completed"
        original_nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
        result = await ReplayRunner(runtime).run(
            task.id,
            cli_principal=current_cli_principal(),
            revision=revision,
            verification_commands=("python -m pytest",),
            regression_commands=("python -m pytest tests",),
            settings=settings,
            output_dir=tmp_path / "experiments",
        )
        assert result.status == "completed" and result.task_id != task.id
        assert result.workspace_revision == revision and result.source_task_id == task.id
        assert (await runtime.get_task(task.id)).status == "completed"
        assert await runtime.storage.metadata.list_execution_nodes(task.id) == original_nodes
        copy = Path(result.evidence_dir) / "repo"
        assert git(copy, "rev-parse", "HEAD") == revision
        assert (copy / "app.py").read_text(encoding="utf-8") == "value = 1\n"
        assert git(repo, "status", "--porcelain") == ""
        artifact = Artifact(
            name="fixture-guidance",
            status=ArtifactStatus.CANDIDATE,
            content="Inspect source carefully before describing its behavior. " * 3,
            evidence_ids=("a", "b", "c"),
        )
        await runtime.storage.metadata.save_artifact(artifact)
        kwargs = dict(
            cli_principal=current_cli_principal(),
            revision=revision,
            verification_commands=("python -m pytest",),
            regression_commands=("python -m pytest tests",),
            settings=settings,
            output_dir=tmp_path / "experiments",
        )
        rejected = await ReplayRunner(runtime).run(
            task.id,
            artifact_snapshot=ArtifactSnapshot(artifact.id, artifact.version, "wrong-hash"),
            **kwargs,
        )
        assert rejected.status == "waiting_resource" and rejected.task_id == ""
        snapshot = ArtifactSnapshot(artifact.id, artifact.version, content_digest(artifact.content))
        compared = await ReplayRunner(runtime).run(task.id, artifact_snapshot=snapshot, **kwargs)
        assert compared.task_id not in {task.id, result.task_id}
        assert compared.status == "completed"
        assert (
            await runtime.storage.metadata.get_artifact(artifact.id)
        ).status is ArtifactStatus.CANDIDATE
        evidence = open_metadata_store(str(Path(compared.evidence_dir) / "metadata.db"))
        try:
            replayed = await evidence.get_task(compared.task_id)
            assert replayed.metadata["replay_candidate_hash"] == snapshot.content_hash
        finally:
            evidence.close()
        from unittest.mock import AsyncMock

        from Sprout.execution.models import ProcessResult

        monkeypatch.setattr(
            "Sprout.execution.docker_verification.DockerVerificationBackend.inspect_image",
            AsyncMock(return_value=ProcessResult(("docker",), allowed=False)),
        )
        coding = await runtime.create_task(
            workspace.id, "Implement a change", execution_mode="code"
        )
        parked = await ReplayRunner(runtime).run(
            coding.id,
            oracle_paths=("app.py",),
            verification_image="sha256:" + "a" * 64,
            **kwargs,
        )
        assert parked.task_id and parked.task_id != coding.id
        assert parked.status == "waiting_resource"
        assert (await runtime.get_task(coding.id)).status == "created"
        assert not await runtime.storage.metadata.list_execution_nodes(coding.id)
        evidence = open_metadata_store(str(Path(parked.evidence_dir) / "metadata.db"))
        try:
            persisted = await evidence.get_task(parked.task_id)
            assert persisted.status == "waiting_resource"
            from Sprout.evolution.replay_plan import verify_plan

            plan = verify_plan(persisted.metadata["replay_plan"])
            assert plan["source_task_id"] == coding.id
            assert plan["revision"] == revision
            assert plan["verification_image"] == "sha256:" + "a" * 64
            assert not await evidence.list_execution_nodes(parked.task_id)
        finally:
            evidence.close()
        from Sprout.evolution.replay_recovery import inspect_recovery

        database = Path(parked.evidence_dir) / "metadata.db"
        before = database.read_bytes()
        recovery = inspect_recovery(database, parked.task_id, settings=settings,
                                    principal=current_cli_principal())
        assert recovery["blockers"] == ["current_runtime_snapshot_required"], recovery
        assert recovery["resume_authorized"] is False
        assert database.read_bytes() == before
        (Path(parked.evidence_dir) / "repo" / "app.py").write_text("changed\n")
        changed = inspect_recovery(database, parked.task_id)
        assert "repository_changed" in changed["blockers"]
        assert "frozen_oracle_changed" in changed["blockers"]
    finally:
        await runtime.stop()


def test_replay_refuses_overlapping_output(tmp_path):
    with pytest.raises(ValueError, match="overlap"):
        prepare(tmp_path, "HEAD", tmp_path / "experiment", _isolated_settings(tmp_path))
