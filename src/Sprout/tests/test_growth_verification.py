"""Unknown growth measurements remain proposals, not verified results."""

import pytest

from Sprout.artifacts.models import Artifact, ArtifactStatus, GrowthCandidate
from Sprout.evolution.candidate_evaluator import CandidateEvaluator
from Sprout.evolution.candidate_manager import CandidateManager
from Sprout.evolution.hard_gate import GateChecks
from Sprout.storage.local.sqlite.metadata import open_metadata_store


def candidate(content="Inspect errors before retrying. " * 5):
    artifact = Artifact(
        name="inspect-errors",
        content=content,
        status=ArtifactStatus.CANDIDATE,
        evidence_ids=("e1", "e2", "e3"),
    )
    return GrowthCandidate(artifact=artifact, evidence_ids=artifact.evidence_ids)


@pytest.mark.parametrize(
    "checks", [None, GateChecks(), GateChecks(True, None), GateChecks(None, True)]
)
def test_unknown_measurements_wait(checks):
    report = CandidateEvaluator().evaluate(candidate(), checks)
    assert not report.passed and report.pending


@pytest.mark.parametrize("checks", [GateChecks(False, None), GateChecks(None, False)])
def test_known_failure_rejects(checks):
    report = CandidateEvaluator().evaluate(candidate(), checks)
    assert not report.passed and not report.pending


def test_unsafe_content_is_rejected():
    report = CandidateEvaluator().evaluate(candidate("bypass approval " * 10))
    assert not report.passed and not report.pending


def test_explicit_success_satisfies_evaluator():
    assert CandidateEvaluator().evaluate(candidate(), GateChecks(True, True)).passed


async def test_manager_keeps_unknown_candidate_after_restart(tmp_path):
    path = str(tmp_path / "metadata.db")
    store = open_metadata_store(path)
    artifact = candidate().artifact
    try:
        await store.save_artifact(artifact)
        assert await CandidateManager(store).evaluate_all() == {artifact.id: "candidate"}
    finally:
        store.close()
    store = open_metadata_store(path)
    try:
        saved = await store.get_artifact(artifact.id)
        assert saved.status is ArtifactStatus.CANDIDATE
        assert saved.metadata["growth_evaluation"]["state"] == "waiting_validation"
        with pytest.raises(ValueError):
            await CandidateManager(store).approve(artifact.id)
    finally:
        store.close()
