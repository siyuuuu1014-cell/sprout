"""Reconstitute the exact candidate registration used by an isolated experiment."""

from Sprout.artifacts.models import ArtifactKind
from Sprout.evolution.isolated_replay import content_digest
from Sprout.evolution.replay_plan import verify_plan
from Sprout.skills.models import Skill, TrustLevel


async def restore_candidate(runtime, task, source_metadata):
    snapshot = verify_plan(task.metadata["replay_plan"])["candidate"]
    if snapshot is None:
        return
    if source_metadata is None:
        raise ValueError("Original candidate authority required")
    artifact = await source_metadata.get_artifact(snapshot["artifact_id"])
    if (artifact is None or artifact.version != snapshot["artifact_version"]
            or content_digest(artifact.content) != snapshot["content_hash"]):
        raise ValueError("Candidate snapshot changed or unavailable")
    if artifact.kind not in {ArtifactKind.SKILL, ArtifactKind.WORKFLOW}:
        raise ValueError("Unsupported candidate kind")
    runtime.skills.register(Skill(name=artifact.name, version=artifact.version,
        instructions=artifact.content, trust=TrustLevel.TRUSTED.value))
