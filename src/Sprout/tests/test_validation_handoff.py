import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from Sprout.artifacts.models import ArtifactStatus
from Sprout.cli.commands.evolution import app
from Sprout.evolution.candidate_manager import CandidateManager
from Sprout.evolution.isolated_replay import content_digest
from Sprout.evolution.validation_handoff import validate_comparison
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.tests.test_comparison_report import paired
from Sprout.tests.test_growth_verification import candidate


async def fixture(tmp_path):
    store = open_metadata_store(str(tmp_path / "authority.db"))
    artifact = candidate().artifact
    await store.save_artifact(artifact)
    pair = paired(tmp_path)
    manifest = json.loads(pair.read_text())
    manifest["candidate"]["artifact_snapshot"] = {"artifact_id": artifact.id,
        "artifact_version": artifact.version, "content_hash": content_digest(artifact.content)}
    pair.write_text(json.dumps(manifest), encoding="utf-8")
    return store, artifact, pair


async def test_incomplete_real_comparison_does_not_promote(tmp_path):
    store, artifact, pair = await fixture(tmp_path)
    try:
        before = await store.get_artifact(artifact.id)
        result = await validate_comparison(store, artifact.id, pair)
        assert result["status"] == "waiting_validation"
        assert result["blockers"] and result["validation_committed"] is False
        assert await store.get_artifact(artifact.id) == before
    finally:
        store.close()


async def test_complete_gate_handoff_persists_report_without_publishing(tmp_path, monkeypatch):
    store, artifact, pair = await fixture(tmp_path)
    # Unit boundary test: complete measurement execution is tested separately;
    # this fixture is not evidence of a real paired Docker success.
    monkeypatch.setattr("Sprout.evolution.validation_handoff.comparison_report", lambda path: {
        "validation_readiness": {"observation_gates_passed": True, "measurement_blockers": []},
        "validation_committed": False})
    try:
        result = await validate_comparison(store, artifact.id, pair)
        assert result["status"] == "validated" and result["validation_committed"] is True
        assert result["published"] is False
    finally:
        store.close()
    store = open_metadata_store(str(tmp_path / "authority.db"))
    try:
        report = await CandidateManager(store).get_candidate_report(artifact.id)
        assert report["validation_current"] is True
        assert report["status"] == ArtifactStatus.VALIDATED.value
        assert report["validation"]["source_run_ids"] == ["baseline", "candidate"]
        assert report["validation"]["comparison"]["pair_fingerprint"] == result["pair_fingerprint"]
        assert not report["publication"]
    finally:
        store.close()


@pytest.mark.parametrize("field", ["content", "version"])
async def test_changed_candidate_refuses_old_pair(tmp_path, field):
    store, artifact, pair = await fixture(tmp_path)
    try:
        changed = replace(artifact, **{field: "changed"})
        await store.save_artifact(changed)
        with pytest.raises(ValueError, match="current candidate"):
            await validate_comparison(store, artifact.id, pair)
        assert (await store.get_artifact(artifact.id)).status == ArtifactStatus.CANDIDATE
    finally:
        store.close()


async def test_cli_reports_missing_measurements_and_closes_resources(tmp_path, monkeypatch):
    store, artifact, pair = await fixture(tmp_path)
    close = AsyncMock()
    monkeypatch.setattr("Sprout.runtime.factory.create_runtime", lambda settings: SimpleNamespace(
        storage=SimpleNamespace(metadata=store, close=close)))
    try:
        result = await asyncio.to_thread(CliRunner().invoke, app,
            ["candidate-validate", artifact.id, "--pair", str(pair)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["status"] == "waiting_validation"
        close.assert_awaited_once()
        assert (await store.get_artifact(artifact.id)).status == ArtifactStatus.CANDIDATE
    finally:
        store.close()
