"""Explicit comparison-to-candidate handoff, never automatic publication."""

from pathlib import Path

from Sprout.evolution.candidate_manager import CandidateManager
from Sprout.evolution.comparison_report import _read, comparison_report
from Sprout.evolution.hard_gate import GateChecks
from Sprout.evolution.isolated_replay import content_digest
from Sprout.evolution.replay_oracle import digest
from Sprout.evolution.validation import artifact_fingerprint


async def validate_comparison(metadata, artifact_id: str, pair_path: Path) -> dict:
    """Recompute the existing evidence gates and commit through the existing CAS."""
    pair_path = pair_path.resolve(strict=True)
    pair = _read(pair_path)
    artifact = await metadata.get_artifact(artifact_id)
    if artifact is None:
        raise LookupError("Candidate not found")
    snapshot = pair.get("candidate", {}).get("artifact_snapshot") or {}
    if (snapshot.get("artifact_id") != artifact.id
            or snapshot.get("artifact_version") != artifact.version
            or snapshot.get("content_hash") != content_digest(artifact.content)):
        raise ValueError("Comparison does not match the current candidate content/version")
    report = comparison_report(pair_path)
    if digest(_read(pair_path)) != digest(pair):
        raise ValueError("Comparison changed during validation")
    readiness = report["validation_readiness"]
    if readiness["observation_gates_passed"] is not True:
        return {"artifact_id": artifact_id, "status": "waiting_validation",
                "validation_committed": False,
                "blockers": readiness["measurement_blockers"], "published": False}
    run_ids = tuple(pair[side]["task_id"] for side in ("baseline", "candidate"))
    if len(set(run_ids)) != 2 or any(not isinstance(run, str) or not run for run in run_ids):
        raise ValueError("Two independent measurement task IDs required")
    evidence = {"schema_version": 1, "pair_path": str(pair_path),
                "pair_fingerprint": digest(pair), "measurement_hashes": pair["measurement_hashes"],
                "report": report,
                "scope": "paired tests/acceptance/regression; not improvement proof"}
    updated = await CandidateManager(metadata).record_validation(artifact_id,
        GateChecks(tests_passed=True, regression_free=True), source_run_ids=run_ids,
        comparison=evidence, expected_fingerprint=artifact_fingerprint(artifact))
    return {"artifact_id": artifact_id, "status": updated.status.value,
            "validation_committed": True, "source_run_ids": list(run_ids),
            "pair_fingerprint": digest(pair), "published": False,
            "next_action": "Inspect candidate-report, then explicitly approve if appropriate"}
