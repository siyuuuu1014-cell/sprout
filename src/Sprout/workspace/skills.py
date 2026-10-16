"""Skills ingestion for Workspace Intelligence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from Sprout.workspace.graph import WorkspaceEdge, WorkspaceNode
from Sprout.workspace.knowledge import ProjectKnowledgeItem
from Sprout.workspace.models import ResourceKind, ResourceRef, WorkspaceGranularity


@dataclass(frozen=True, slots=True)
class SkillWorkspaceAnalysis:
    nodes: tuple[WorkspaceNode, ...] = ()
    edges: tuple[WorkspaceEdge, ...] = ()
    knowledge_items: tuple[ProjectKnowledgeItem, ...] = ()
    resource_hashes: Mapping[str, str] = field(default_factory=dict)


class SkillWorkspaceAnalyzer:
    """Describe loadable artifacts without granting trust or executing them."""

    def analyze(self, workspace_id: str, skills_dir: str | Path) -> SkillWorkspaceAnalysis:
        # Skills package assembly imports security, which imports workspace
        # models. Delay the capability import until analysis to avoid a cycle.
        from Sprout.skills.loader import iter_skill_artifacts

        root = Path(skills_dir).resolve()
        if not root.exists():
            return SkillWorkspaceAnalysis()

        nodes: list[WorkspaceNode] = []
        edges: list[WorkspaceEdge] = []
        knowledge_items: list[ProjectKnowledgeItem] = []
        resource_hashes: dict[str, str] = {}
        tool_ids: set[str] = set()
        for skill, artifact in sorted(iter_skill_artifacts(root), key=lambda item: str(item[1])):
            path = artifact if artifact.is_file() else artifact / "SKILL.md"
            name = skill.name
            description = skill.description
            evidence = path.resolve().as_posix()
            # Location distinguishes same-name installations; content updates
            # preserve identity while changing the enclosing snapshot version.
            identity = sha256(str(artifact.resolve()).encode()).hexdigest()[:32]
            resource_hashes[evidence] = skill.digest

            skill_node = WorkspaceNode(
                id=f"{workspace_id}:skill:{identity}",
                resource=ResourceRef(
                    workspace_id=workspace_id,
                    path=evidence,
                    content_hash=skill.digest,
                    kind=ResourceKind.DOCUMENTATION,
                ),
                kind="skill",
                name=name,
                qualified_name=name,
                granularity=WorkspaceGranularity.SKILL.value,
            )
            nodes.append(skill_node)
            for tool in sorted(set(skill.required_tools)):
                tool_node = WorkspaceNode(
                    id=f"{workspace_id}:tool:{tool}",
                    kind="tool",
                    name=tool,
                    qualified_name=tool,
                    granularity=WorkspaceGranularity.SKILL.value,
                )
                if tool_node.id not in tool_ids:
                    nodes.append(tool_node)
                    tool_ids.add(tool_node.id)
                edges.append(
                    WorkspaceEdge(
                        id=f"{skill_node.id}->{tool_node.id}:requires",
                        source=skill_node.id,
                        target=tool_node.id,
                        relation="requires",
                        evidence=(evidence,),
                    )
                )
            knowledge_items.append(
                ProjectKnowledgeItem(
                    id=f"{workspace_id}:skill-knowledge:{identity}",
                    statement=f"Skill: {name}"
                    + (f" - {description}" if description else ""),
                    kind="skill",
                    scope="skill",
                    evidence_ids=(evidence,),
                    confidence=1.0,
                )
            )

        return SkillWorkspaceAnalysis(
            nodes=tuple(nodes),
            edges=tuple(edges),
            knowledge_items=tuple(knowledge_items),
            resource_hashes=resource_hashes,
        )


__all__ = ["SkillWorkspaceAnalysis", "SkillWorkspaceAnalyzer"]
