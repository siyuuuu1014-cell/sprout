"""Publication fault injection uses trusted fixed measurement fixtures only."""

import asyncio
from dataclasses import replace

import pytest

from Sprout.artifacts.models import Artifact, ArtifactStatus
from Sprout.evolution.candidate_manager import CandidateManager
from Sprout.evolution.hard_gate import GateChecks
from Sprout.skills.registry import SkillRegistry
from Sprout.skills.repository import SkillRepository
from Sprout.storage.local.sqlite.metadata import open_metadata_store


async def fixture(tmp_path):
    store = open_metadata_store(str(tmp_path / "control.db"))
    artifact = Artifact(
        name="fixture-skill",
        status=ArtifactStatus.CANDIDATE,
        content="Inspect error evidence before retrying. " * 4,
        evidence_ids=("a", "b", "c"),
    )
    await store.save_artifact(artifact)
    manager = CandidateManager(
        store, skills=SkillRegistry(), skill_repository=SkillRepository(tmp_path / "skills")
    )
    await manager.record_validation(
        artifact.id, GateChecks(True, True), source_run_ids=("trusted-fixture-run",)
    )
    return store, manager, await store.get_artifact(artifact.id)


async def test_historical_validated_without_report_is_blocked(tmp_path):
    store = open_metadata_store(str(tmp_path / "control.db"))
    try:
        artifact = Artifact(status=ArtifactStatus.VALIDATED)
        await store.save_artifact(artifact)
        with pytest.raises(ValueError, match="validation"):
            await CandidateManager(store).approve(artifact.id)
    finally:
        store.close()


async def test_imported_validation_metadata_is_rejected(tmp_path):
    store = open_metadata_store(str(tmp_path / "control.db"))
    try:
        with pytest.raises(ValueError, match="handoff"):
            await store.save_artifact(Artifact(metadata={"growth_validation": {"schema": 1}}))
    finally:
        store.close()


async def test_content_change_invalidates_report(tmp_path):
    store, manager, artifact = await fixture(tmp_path)
    try:
        await store.save_artifact(replace(artifact, content=artifact.content + " Changed."))
        with pytest.raises(ValueError, match="validation"):
            await manager.approve(artifact.id)
    finally:
        store.close()


async def test_partial_effect_failure_survives_restart(tmp_path, monkeypatch):
    store, manager, artifact = await fixture(tmp_path)
    calls = []

    def fail(skill):
        calls.append(skill.name)
        (tmp_path / "effect.txt").write_text("partial", encoding="utf-8")
        raise OSError("Interrupted after side effect")

    monkeypatch.setattr(manager._skill_repository, "save", fail)
    try:
        with pytest.raises(OSError):
            await manager.approve(artifact.id, decided_by="tester")
        saved = await store.get_artifact(artifact.id)
        assert saved.status is ArtifactStatus.VALIDATED
        assert saved.metadata["publication_guard"]["state"] == "uncertain"
        assert saved.metadata["publication_guard"]["decided_by"] == "tester"
    finally:
        store.close()
    store = open_metadata_store(str(tmp_path / "control.db"))
    try:
        report = await CandidateManager(store).get_candidate_report(artifact.id)
        assert report["requires_manual_review"]
        assert report["publication"]["state"] == "uncertain"
        with pytest.raises(ValueError, match="occupied"):
            await CandidateManager(store).approve(artifact.id)
        assert calls == [artifact.name]
    finally:
        store.close()


async def test_concurrent_approval_does_not_repeat_effect(tmp_path, monkeypatch):
    store, manager, artifact = await fixture(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def publish(updated):
        calls.append(updated.id)
        entered.set()
        await release.wait()

    monkeypatch.setattr(manager, "_publish", publish)
    first = asyncio.create_task(manager.approve(artifact.id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(ValueError, match="occupied"):
            await manager.approve(artifact.id)
        release.set()
        result = await first
        assert result.status is ArtifactStatus.PUBLISHED
        assert result.metadata["publication_guard"]["state"] == "completed"
        assert calls == [artifact.id]
        with pytest.raises(ValueError):
            await manager.approve(artifact.id)
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)
        store.close()


async def test_stale_save_cannot_clear_active_claim(tmp_path):
    store, _, artifact = await fixture(tmp_path)
    try:
        assert await store.claim_artifact_publication(artifact, "owner")
        with pytest.raises(ValueError, match="occupied"):
            await store.save_artifact(artifact)
        assert (await store.get_artifact(artifact.id)).metadata["publication_guard"][
            "owner"
        ] == "owner"
        assert not await store.claim_artifact_publication(artifact, "other")
    finally:
        store.close()


async def test_occupied_publication_rejects_revalidation(tmp_path):
    store, manager, artifact = await fixture(tmp_path)
    try:
        assert await store.claim_artifact_publication(artifact, "owner")
        with pytest.raises(ValueError, match="occupied"):
            await manager.record_validation(
                artifact.id, GateChecks(True, True), source_run_ids=("another-fixture",)
            )
    finally:
        store.close()


async def test_independent_connections_compete_atomically(tmp_path):
    store, _, artifact = await fixture(tmp_path)
    peer = open_metadata_store(str(tmp_path / "control.db"))
    try:
        results = await asyncio.gather(
            store.claim_artifact_publication(artifact, "first"),
            peer.claim_artifact_publication(artifact, "second"),
        )
        assert sum(results) == 1
        sibling = replace(
            artifact,
            id="another-id",
            name=artifact.name.upper(),
            status=ArtifactStatus.CANDIDATE,
            metadata={},
        )
        await store.save_artifact(sibling)
        sibling = await CandidateManager(store).record_validation(
            sibling.id, GateChecks(True, True), source_run_ids=("independent-fixture",)
        )
        assert not await peer.claim_artifact_publication(sibling, "different-id")
    finally:
        peer.close()
        store.close()


async def test_completion_commit_failure_is_not_published(tmp_path, monkeypatch):
    store, manager, artifact = await fixture(tmp_path)
    finish = store.finish_artifact_publication

    async def fail_commit(updated, owner, state):
        return False if state == "completed" else await finish(updated, owner, state)

    monkeypatch.setattr(store, "finish_artifact_publication", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="committed"):
            await manager.approve(artifact.id)
        assert manager._skill_repository.exists(artifact.name)
        saved = await store.get_artifact(artifact.id)
        assert saved.status is ArtifactStatus.VALIDATED
        assert saved.metadata["publication_guard"]["state"] == "uncertain"
        with pytest.raises(ValueError, match="occupied"):
            await manager.approve(artifact.id)
    finally:
        store.close()


async def test_cancelled_publisher_retains_unknown_claim(tmp_path, monkeypatch):
    store, manager, artifact = await fixture(tmp_path)
    entered = asyncio.Event()

    async def publish(updated):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_publish", publish)
    running = asyncio.create_task(manager.approve(artifact.id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        saved = await store.get_artifact(artifact.id)
        assert saved.metadata["publication_guard"]["state"] == "uncertain"
        assert saved.status is ArtifactStatus.VALIDATED
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        store.close()


async def test_published_artifact_keeps_lifecycle_working(tmp_path):
    store, manager, artifact = await fixture(tmp_path)
    try:
        await manager.approve(artifact.id)
        assert (await manager.mark_monitoring(artifact.id)).status is ArtifactStatus.MONITORING
        assert (await manager.mark_stable(artifact.id)).status is ArtifactStatus.STABLE
        assert (await manager.deprecate(artifact.id)).status is ArtifactStatus.DEPRECATED
    finally:
        store.close()
