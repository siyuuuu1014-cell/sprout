"""Bind trusted internal measurement handoffs to an exact candidate snapshot."""

import hashlib
import json

from Sprout.artifacts.models import Artifact


def artifact_fingerprint(artifact: Artifact) -> str:
    metadata = {
        key: value
        for key, value in artifact.metadata.items()
        if key
        not in {
            "growth_validation",
            "growth_evaluation",
            "publication_guard",
            "decided_by",
            "publication_error",
        }
    }
    snapshot = {
        "id": artifact.id,
        "kind": artifact.kind.value,
        "name": artifact.name,
        "version": artifact.version,
        "scope": artifact.scope,
        "content": artifact.content,
        "evidence_ids": artifact.evidence_ids,
        "metadata": metadata,
    }
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
