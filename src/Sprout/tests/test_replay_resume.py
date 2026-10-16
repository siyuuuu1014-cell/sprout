import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from Sprout.evolution.isolated_replay import git, isolated_settings
from Sprout.evolution.replay_config import configuration_contract
from Sprout.evolution.replay_plan import build_plan
from Sprout.evolution.replay_resume import resume_replay
from Sprout.evolution.replay_snapshot import runtime_snapshot
from Sprout.execution.models import ProcessResult
from Sprout.gateway.local_identity import creator_record, current_cli_principal
from Sprout.runtime.factory import create_runtime
from Sprout.runtime.runtime import Runtime
from Sprout.task.models import TaskResult, TaskStatus
from Sprout.tests.test_temporal_integration import _isolated_settings


@pytest.fixture(autouse=True)
def assembled_coding_dispatch_stub(monkeypatch):
    """Recovery tests mock the coding graph; explicitly mock its required dispatch too."""
    monkeypatch.setattr(Runtime, "_isolated_coding_dispatch", AsyncMock(), raising=False)


async def fixture(tmp_path, image=None):
    settings = _isolated_settings(tmp_path / "source-runtime")
    directory = tmp_path / "experiment"
    repo = directory / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "test_app.py").write_text("def test_value(): assert 1 == 1\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", "baseline")
    original = tmp_path / "original"
    original.mkdir()
    isolated = isolated_settings(directory, settings)
    runtime = create_runtime(isolated)
    principal = current_cli_principal()
    try:
        await runtime.start()
        workspace = await runtime.open_workspace(repo)
        from Sprout.evolution.replay_oracle import snapshot

        plan = build_plan(
            source_task_id="source", revision=git(repo, "rev-parse", "HEAD"), mode="code",
            original=original, repo=repo, tests=("pytest test_app.py",),
            regressions=("pytest test_app.py",), oracle_paths=("test_app.py",),
            frozen=snapshot(repo, ("test_app.py",)), image=image or "sha256:" + "a" * 64,
            configuration=configuration_contract(isolated), runtime=runtime_snapshot(runtime),
            creator=creator_record(principal),
        )
        task = await runtime.create_task(workspace.id, "Fixture", actor=principal, source="cli",
                                         execution_mode="code", metadata={
            "replay_stage": "image_preflight", "replay_source_task_id": "source",
            "replay_plan": plan,
        })
        await runtime.storage.metadata.update_task_status(task.id, TaskStatus.WAITING_RESOURCE)
    finally:
        await runtime.stop()
    return directory / "metadata.db", task, settings, principal


def ready():
    return ProcessResult(("docker",), exit_code=0, stdout=json.dumps([{
        "Os": "linux", "Config": {"Labels": {
            "sprout.role": "independent-verification", "sprout.verification.schema": "1",
        }},
    }]))


async def test_resume_waits_without_claim_when_image_unapproved(tmp_path, monkeypatch):
    database, task, settings, principal = await fixture(tmp_path)
    inspect = AsyncMock(return_value=ProcessResult(("docker",), allowed=False))
    monkeypatch.setattr("Sprout.execution.docker_verification.DockerVerificationBackend.inspect_image",
                        inspect)
    execute = AsyncMock()
    monkeypatch.setattr(Runtime, "execute", execute)
    result = await resume_replay(database, task.id, settings=settings, principal=principal)
    assert result["reason"] == "image_unavailable_unapproved_or_incompatible"
    execute.assert_not_awaited()
    from Sprout.storage.local.sqlite.metadata import open_metadata_store

    store = open_metadata_store(str(database))
    try:
        assert await store.get_replay_resume(task.id) is None
    finally:
        store.close()


async def test_resume_runs_same_task_once_and_does_not_validate(tmp_path, monkeypatch):
    database, task, settings, principal = await fixture(tmp_path)
    monkeypatch.setattr("Sprout.execution.docker_verification.DockerVerificationBackend.inspect_image",
                        AsyncMock(return_value=ready()))
    execute = AsyncMock(return_value=TaskResult(task_id=task.id, status=TaskStatus.COMPLETED))
    monkeypatch.setattr(Runtime, "execute", execute)
    measure = AsyncMock(return_value=[{"waiting": True, "reason": "fixture_wait"}])
    monkeypatch.setattr("Sprout.evolution.replay_docker.measure_docker", measure)
    result = await resume_replay(database, task.id, settings=settings, principal=principal)
    assert result["validation_committed"] is False
    assert result["measurement_status"] == "waiting_resource"
    assert execute.call_args.args[0].id == task.id
    assert execute.call_args.kwargs["replay_resume_owner"]
    again = await resume_replay(database, task.id, settings=settings, principal=principal)
    assert "existing_resume_ownership" in again["reason"]
    assert execute.await_count == 1


@pytest.mark.parametrize("error", [RuntimeError("interrupted"), asyncio.CancelledError()])
async def test_resume_unknown_result_blocks_reexecution(tmp_path, monkeypatch, error):
    database, task, settings, principal = await fixture(tmp_path)
    monkeypatch.setattr("Sprout.execution.docker_verification.DockerVerificationBackend.inspect_image",
                        AsyncMock(return_value=ready()))
    execute = AsyncMock(side_effect=error)
    monkeypatch.setattr(Runtime, "execute", execute)
    with pytest.raises(type(error)):
        await resume_replay(database, task.id, settings=settings, principal=principal)
    again = await resume_replay(database, task.id, settings=settings, principal=principal)
    assert "existing_resume_ownership" in again["reason"]
    assert execute.await_count == 1


async def test_direct_runtime_execute_and_resume_cannot_bypass_recovery(tmp_path):
    database, task, settings, _ = await fixture(tmp_path)
    runtime = create_runtime(isolated_settings(database.parent, settings))
    try:
        with pytest.raises(ValueError, match="dedicated recovery"):
            await runtime.execute(task)  # Deliberately stale CREATED task argument.
        with pytest.raises(ValueError, match="dedicated recovery"):
            await runtime.resume_task(task)
        assert not await runtime.storage.metadata.list_execution_nodes(task.id)
    finally:
        await runtime.stop()


async def test_concurrent_resume_has_one_execution(tmp_path, monkeypatch):
    database, task, settings, principal = await fixture(tmp_path)
    monkeypatch.setattr("Sprout.execution.docker_verification.DockerVerificationBackend.inspect_image",
                        AsyncMock(return_value=ready()))
    execute = AsyncMock(return_value=TaskResult(task_id=task.id, status=TaskStatus.FAILED))
    monkeypatch.setattr(Runtime, "execute", execute)
    results = await asyncio.gather(*[
        resume_replay(database, task.id, settings=settings, principal=principal)
        for _ in range(2)
    ])
    assert execute.await_count == 1
    assert sum(result["status"] == "failed" for result in results) == 1


async def test_explicit_approval_uses_existing_broker_then_resumes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from Sprout.security.access import AccessDecision, PolicyDecision

    database, task, settings, principal = await fixture(tmp_path)
    execute = AsyncMock(return_value=TaskResult(task_id=task.id, status=TaskStatus.FAILED))
    monkeypatch.setattr(Runtime, "execute", execute)
    spawn = AsyncMock(return_value=SimpleNamespace(returncode=0,
        communicate=AsyncMock(return_value=(ready().stdout.encode(), b""))))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)

    def factory(config):
        runtime = create_runtime(config)
        runtime._process_broker._policy = SimpleNamespace(decide=lambda request: PolicyDecision(
            AccessDecision.REQUIRE_APPROVAL, "fixture needs review"))
        return runtime

    first = await resume_replay(database, task.id, settings=settings, principal=principal,
                                runtime_factory=factory)
    assert first["approval_id"]
    execute.assert_not_awaited()
    spawn.assert_not_awaited()
    second = await resume_replay(database, task.id, settings=settings, principal=principal,
                                 approval_id=first["approval_id"], runtime_factory=factory)
    assert second["status"] == "failed"
    assert execute.await_count == spawn.await_count == 1


def test_cli_dispatches_explicit_resume_without_user_identity_option(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from Sprout.cli.commands.evolution import app

    database, task, settings, _ = asyncio.run(fixture(tmp_path))
    monkeypatch.setattr("Sprout.config.loader.load_settings", lambda config: settings)
    resume = AsyncMock(return_value={"status": "waiting_resource", "validation_committed": False})
    monkeypatch.setattr("Sprout.evolution.replay_resume.resume_replay", resume)
    result = CliRunner().invoke(app, ["replay-resume", task.id, "--database", str(database)])
    assert result.exit_code == 0, result.output
    assert resume.await_count == 1
    assert resume.call_args.kwargs["principal"].authenticated


async def test_recovery_owner_passes_real_runtime_entry_guard(tmp_path, monkeypatch):
    database, task, settings, principal = await fixture(tmp_path)
    monkeypatch.setattr("Sprout.execution.docker_verification.DockerVerificationBackend.inspect_image",
                        AsyncMock(return_value=ready()))
    execute_graph = AsyncMock(return_value=TaskResult(task_id=task.id, status=TaskStatus.FAILED))
    monkeypatch.setattr(Runtime, "_execute_task", execute_graph)
    result = await resume_replay(database, task.id, settings=settings, principal=principal)
    assert result["status"] == "failed"
    assert execute_graph.await_count == 1


async def test_settlement_failure_keeps_owner_and_blocks_retry(tmp_path, monkeypatch):
    from Sprout.storage.local.sqlite.metadata import SqliteMetadataStore

    database, task, settings, principal = await fixture(tmp_path)
    monkeypatch.setattr("Sprout.execution.docker_verification.DockerVerificationBackend.inspect_image",
                        AsyncMock(return_value=ready()))
    execute = AsyncMock(return_value=TaskResult(task_id=task.id, status=TaskStatus.FAILED))
    monkeypatch.setattr(Runtime, "execute", execute)
    monkeypatch.setattr(SqliteMetadataStore, "finish_replay_resume", AsyncMock(return_value=False))
    result = await resume_replay(database, task.id, settings=settings, principal=principal)
    assert result["reason"] == "resume_settlement_unconfirmed"
    again = await resume_replay(database, task.id, settings=settings, principal=principal)
    assert "existing_resume_ownership" in again["reason"]
    assert execute.await_count == 1


def test_recovery_assembly_isolates_all_storage_lanes(tmp_path):
    settings = _isolated_settings(tmp_path / "source")
    settings.storage.cache = "redis://original"
    settings.storage.vectors = "milvus://original"
    settings.storage.graph = "neo4j://original"
    isolated = isolated_settings(tmp_path / "experiment", settings)
    for lane in ("operational", "metadata", "session", "context", "usage"):
        assert "/experiment/" in getattr(isolated.storage, lane)
    assert isolated.storage.cache == isolated.storage.vectors == "memory"
    assert isolated.storage.graph == "none"
    assert isolated.memory.home == str(tmp_path / "experiment" / "memory")
    assert settings.storage.cache == "redis://original"


async def test_missing_coding_dispatch_does_not_claim_resume(tmp_path, monkeypatch):
    database, task, settings, principal = await fixture(tmp_path)
    monkeypatch.delattr(Runtime, "_isolated_coding_dispatch")
    monkeypatch.setattr("Sprout.execution.docker_verification.DockerVerificationBackend.inspect_image",
                        AsyncMock(return_value=ready()))
    execute = AsyncMock()
    monkeypatch.setattr(Runtime, "execute", execute)
    result = await resume_replay(database, task.id, settings=settings, principal=principal)
    assert result["reason"] == "isolated_coding_backend_not_assembled"
    execute.assert_not_called()
    from Sprout.storage.local.sqlite.metadata import open_metadata_store

    store = open_metadata_store(str(database))
    try:
        assert await store.get_replay_resume(task.id) is None
        assert not await store.list_execution_nodes(task.id)
    finally:
        store.close()
