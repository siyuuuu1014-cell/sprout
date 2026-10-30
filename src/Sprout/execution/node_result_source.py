"""Versioned project node provenance, not a portable authorization token."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


def payload_digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NodeResultSourceV1:
    task_id: str
    node_id: str
    node_type: str
    owner: str
    subject_digest: str
    output_digest: str
    resources_json: str
    parents_json: str
    version: int = 1

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, allow_nan=False)

    @classmethod
    def from_json(cls, value):
        record = cls(**json.loads(value))
        if (record.version != 1 or type(record.version) is not int
                or not all(type(x) is str and x for x in (
                    record.task_id, record.node_id, record.node_type, record.owner))
                or any(len(x) != 64 or any(c not in "0123456789abcdef" for c in x)
                       for x in (record.subject_digest, record.output_digest))
                or type(json.loads(record.resources_json)) is not list
                or type(json.loads(record.parents_json)) is not list):
            raise ValueError("Invalid node source")
        from Sprout.execution.file_snapshot import ResourceIdentity, valid_identity
        from Sprout.workspace.authorized_content import relative_label

        for resource in json.loads(record.resources_json):
            if (type(resource) is not dict
                    or set(resource) != {"path", "kind", "digest", "identity"}
                    or type(resource["digest"]) is not str
                    or len(resource["digest"]) != 64
                    or any(c not in "0123456789abcdef" for c in resource["digest"])):
                raise ValueError("Invalid source resource")
            relative_label(resource["path"])
            if not valid_identity(ResourceIdentity(**resource["identity"])):
                raise ValueError("Invalid source identity")
        for parent in json.loads(record.parents_json):
            if (type(parent) is not dict or set(parent) != {"node_id", "output_digest"}
                    or type(parent["node_id"]) is not str or not parent["node_id"]
                    or type(parent["output_digest"]) is not str
                    or len(parent["output_digest"]) != 64):
                raise ValueError("Invalid parent source")
        return record
