"""Stable source declaration evidence shared by static analyzers."""

import json
from hashlib import sha256

from Sprout.workspace.knowledge import ProjectKnowledgeItem


def source_fact(workspace, path, line, kind, statement, details):
    evidence = path.resolve().as_posix()
    identity = json.dumps([kind, evidence, line, statement], ensure_ascii=False)
    return ProjectKnowledgeItem(
        id=f"{workspace.id}:contract:" + sha256(identity.encode()).hexdigest()[:32],
        statement=statement, kind=kind, evidence_ids=(evidence,), confidence=1.0,
        details={"path": evidence, "line": line, **details},
    )
