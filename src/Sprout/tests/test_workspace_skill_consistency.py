"""Skill ontology identity, loader parity, and external-input invalidation."""

import asyncio
from pathlib import Path

from Sprout.runtime.workspaces import WorkspaceService
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.metadata import SqliteMetadataStore
from Sprout.storage.local.sqlite.projects import SqliteProjectAnalysisStore
from Sprout.task.models import Task
from Sprout.workspace.intelligence import WorkspaceIntelligence
from Sprout.workspace.invalidation import snapshot_id
from Sprout.workspace.models import Workspace
from Sprout.workspace.skills import SkillWorkspaceAnalyzer


def _skill(root: Path, relative: str, description="Original") -> Path:
    path = root / relative / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nname: same-name\ndescription: {description}\n'
        'required_tools: ["file.read", "search", "search"]\n---\n\nBody\n',
        encoding="utf-8",
    )
    return path


def test_same_named_skills_keep_unique_ids_and_share_tool_nodes(tmp_path):
    first = _skill(tmp_path, "one")
    second = _skill(tmp_path, "two")
    analysis = SkillWorkspaceAnalyzer().analyze("project", tmp_path)
    skills = [node for node in analysis.nodes if node.kind == "skill"]
    tools = [node for node in analysis.nodes if node.kind == "tool"]
    assert len(skills) == len({node.id for node in skills}) == 2
    assert {node.name for node in tools} == {"file.read", "search"}
    assert len(tools) == 2
    assert len(analysis.edges) == len({edge.id for edge in analysis.edges}) == 4
    assert len({item.id for item in analysis.knowledge_items}) == 2
    assert {edge.evidence for edge in analysis.edges} == {
        (first.resolve().as_posix(),), (second.resolve().as_posix(),),
    }
    assert analysis == SkillWorkspaceAnalyzer().analyze("project", tmp_path)


def test_workspace_skill_scan_matches_shared_loader_and_excludes_quarantine(tmp_path):
    _skill(tmp_path, ".quarantine/hidden")
    _skill(tmp_path, ".fetched/downloaded")
    _skill(tmp_path, ".evolved/generated")
    (tmp_path / "local.toml").write_text(
        'name="toml-skill"\ninstructions="Read only"\nrequired_tools=["file.read"]\n',
        encoding="utf-8",
    )
    analysis = SkillWorkspaceAnalyzer().analyze("project", tmp_path)
    assert len(analysis.knowledge_items) == 3
    assert any("toml-skill" in item.statement for item in analysis.knowledge_items)
    assert not any(".quarantine" in evidence for item in analysis.knowledge_items
                   for evidence in item.evidence_ids)


def test_external_skill_changes_invalidate_persisted_analysis(tmp_path):
    async def run():
        root = tmp_path / "source"
        root.mkdir()
        (root / "app.py").write_text("value = 1\n", encoding="utf-8")
        skills = tmp_path / "external-skills"
        first_path = _skill(skills, "one")
        _skill(skills, "two")
        workspace = Workspace(id="project", root=root)
        task = Task(workspace_id=workspace.id, instruction="analyze")
        intelligence = WorkspaceIntelligence(skills_dir=str(skills))
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            first = await intelligence.analyze(workspace, task)
            version = await store.save_analysis(first, expected_snapshot_id=None)
        finally:
            store.close()
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            restored = await store.load_analysis(workspace.id)
            assert len([item for item in restored.knowledge.items if item.kind == "skill"]) == 2
            unchanged = await intelligence.analyze(workspace, task, previous=restored)
            assert unchanged.fingerprint == restored.fingerprint
            _skill(skills, "one", "Edited")
            changed = await intelligence.analyze(workspace, task, previous=restored)
            assert snapshot_id(changed) != version
            assert first_path.resolve().as_posix() in changed.changed_resources
            old = next(item for item in restored.knowledge.items
                       if item.evidence_ids == (first_path.resolve().as_posix(),))
            assert old.id in changed.stale_knowledge_ids
            assert any("Edited" in item.statement for item in changed.knowledge.items)
            asset = first_path.parent / "notes.txt"
            asset.write_text("Changed dependency\n", encoding="utf-8")
            asset_changed = await intelligence.analyze(workspace, task, previous=changed)
            assert snapshot_id(asset_changed) != snapshot_id(changed)
            removed_node_id = next(
                node.id for node in asset_changed.graph.nodes
                if node.kind == "skill"
                and node.resource.path == first_path.resolve().as_posix()
            )
            first_path.unlink()
            removed = await intelligence.analyze(workspace, task, previous=asset_changed)
            assert first_path.resolve().as_posix() in removed.changed_resources
            assert not any(node.id == removed_node_id for node in removed.graph.nodes)
            assert len([item for item in removed.knowledge.items if item.kind == "skill"]) == 1
            new_version = await store.save_analysis(removed, expected_snapshot_id=version)
        finally:
            store.close()
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            final = await store.load_analysis(workspace.id)
            assert snapshot_id(final) == new_version
            assert len([item for item in final.knowledge.items if item.kind == "skill"]) == 1
        finally:
            store.close()

    asyncio.run(run())


async def test_service_rechecks_external_skills_after_local_and_hot_cache_hits(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    skills = tmp_path / "skills"
    _skill(skills, "one")
    storage = StorageBundle.in_memory()
    storage.metadata = SqliteMetadataStore(SqliteDatabase.open(tmp_path / "runtime.db"))
    storage.projects = SqliteProjectAnalysisStore(tmp_path / "projects")
    try:
        service = WorkspaceService(storage, skills_dir=str(skills))
        workspace = await service.open(root, workspace_id="project")
        first = await service.analyze(workspace.id)
        _skill(skills, "one", "Edited after local cache")
        second = await service.analyze(workspace.id)
        assert snapshot_id(first) != snapshot_id(second)
        _skill(skills, "one", "Edited after hot cache")
        fresh_service = WorkspaceService(storage, skills_dir=str(skills))
        third = await fresh_service.analyze(workspace.id)
        assert snapshot_id(third) != snapshot_id(second)
        assert any("Edited after hot cache" in item.statement for item in third.knowledge.items)
        assert await storage.projects.current_snapshot_id("project") == snapshot_id(third)
        without_skills = await WorkspaceService(storage).analyze(workspace.id)
        assert not any(item.kind == "skill" for item in without_skills.knowledge.items)
        assert snapshot_id(without_skills) != snapshot_id(third)
    finally:
        await storage.close()
