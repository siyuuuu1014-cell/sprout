"""Create a successor comparison from completed measurements; preserve old evidence."""

from pathlib import Path

from Sprout.evolution.comparison_report import _read, comparison_report
from Sprout.evolution.replay_measurement import COMPLETED_MEASUREMENT
from Sprout.evolution.replay_pair import write_pair
from Sprout.evolution.replay_runner import ReplayResult
from Sprout.storage.local.sqlite.verification_report import recovery_facts
from Sprout.task.models import TaskStatus
from Sprout.trajectory.models import ArtifactSnapshot


def refresh_comparison(pair_path):
    pair_path = Path(pair_path)
    pair = _read(pair_path)
    if pair.get("schema_version") != 1:
        raise ValueError("Unsupported comparison schema")
    results = []
    for role in ("baseline", "candidate"):
        previous = pair[role]
        directory = Path(previous["evidence_dir"]).resolve(strict=True)
        path = directory / COMPLETED_MEASUREMENT
        measured = _read(path)
        facts = recovery_facts(directory / "metadata.db", previous["task_id"])
        if (facts["status"] != "completed" or measured.get("task_status") != "completed"
                or measured.get("task_id") != previous["task_id"]
                or measured.get("source_task_id") != previous["source_task_id"]
                or measured.get("revision") != previous["workspace_revision"]
                or measured.get("measurement_status") != "observed"):
            raise ValueError("Both original experiments require completed independent measurements")
        snapshot = previous.get("artifact_snapshot")
        results.append(ReplayResult(**{**previous, "status": TaskStatus.COMPLETED, "error": None,
            "artifact_snapshot": ArtifactSnapshot(**snapshot) if snapshot else None,
            "measurement_path": str(path), "measurement_status": "observed"}))
    successor = write_pair(pair_path.resolve().parent, *results)
    return {"previous_pair": str(pair_path), "pair_manifest": successor,
            "report": comparison_report(Path(successor)), "validation_committed": False}
