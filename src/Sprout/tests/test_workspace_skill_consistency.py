"""Skill ontology identity, loader parity, and external-input invalidation."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from Sprout.runtime.workspaces import WorkspaceService
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.metadata import SqliteMetadataStore
from Sprout.storage.local.sqlite.projects import SqliteProjectAnalysisStore
from Sprout.task.models import Task
from Sprout.tests.readonly_host_fixtures import fixture_analysis
from Sprout.workspace.intelligence import WorkspaceIntelligence
from Sprout.workspace.invalidation import snapshot_id
from Sprout.workspace.models import Workspace
from Sprout.workspace.skills import SkillWorkspaceAnalyzer


async def _skill_snapshot(workspace, task, skills, previous=None):
    """Explicit composition of host Skill DATA for store/cache component tests.

    Restricted Workspace analysis never discovers these external files and
    this composition is not a Runtime publication receipt or trust grant.
    """
    intelligence = WorkspaceIntelligence()
    base = await fixture_analysis(intelligence, workspace, task, paths=("app.py",))
    found = SkillWorkspaceAnalyzer().analyze(workspace.id, skills)
    fingerprint = replace(base.fingerprint, resource_hashes={
        **base.fingerprint.resource_hashes, **found.resource_hashes})
    changed = fingerprint.changed_resources(previous.fingerprint if previous else None)
    stale = intelligence._stale_knowledge_ids(previous, set(changed))
    return replace(base, fingerprint=fingerprint, changed_resources=changed,
        stale_knowledge_ids=stale,
        stale_knowledge_items=intelligence._stale_knowledge_items(previous, stale),
        graph=replace(base.graph, nodes=base.graph.nodes + found.nodes,
                      edges=base.graph.edges + found.edges),
        knowledge=replace(base.knowledge, items=base.knowledge.items + found.knowledge_items))


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
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            first = await _skill_snapshot(workspace, task, skills)
            version = await store.save_analysis(first, expected_snapshot_id=None)
        finally:
            store.close()
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            restored = await store.load_analysis(workspace.id)
            assert len([item for item in restored.knowledge.items if item.kind == "skill"]) == 2
            unchanged = await _skill_snapshot(workspace, task, skills, restored)
            assert unchanged.fingerprint.resource_hashes == restored.fingerprint.resource_hashes
            assert unchanged.changed_resources == ()
            _skill(skills, "one", "Edited")
            changed = await _skill_snapshot(workspace, task, skills, restored)
            assert snapshot_id(changed) != version
            assert first_path.resolve().as_posix() in changed.changed_resources
            old = next(item for item in restored.knowledge.items
                       if item.evidence_ids == (first_path.resolve().as_posix(),))
            assert old.id in changed.stale_knowledge_ids
            assert any("Edited" in item.statement for item in changed.knowledge.items)
            asset = first_path.parent / "notes.txt"
            asset.write_text("Changed dependency\n", encoding="utf-8")
            asset_changed = await _skill_snapshot(workspace, task, skills, changed)
            assert snapshot_id(asset_changed) != snapshot_id(changed)
            removed_node_id = next(
                node.id for node in asset_changed.graph.nodes
                if node.kind == "skill"
                and node.resource.path == first_path.resolve().as_posix()
            )
            first_path.unlink()
            removed = await _skill_snapshot(workspace, task, skills, asset_changed)
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
        task = Task(workspace_id=workspace.id, instruction="fixture assets")
        # A legacy cache hit cannot authorize automatic external Skill access.
        with pytest.raises(PermissionError, match="authorized_workspace_analysis_required"):
            await service.analyze(workspace.id)
        from Sprout.workspace.cache import WorkspaceAnalysisCache

        cache = WorkspaceAnalysisCache()
        first = await _skill_snapshot(workspace, task, skills)
        version = await storage.projects.save_analysis(first, expected_snapshot_id=None)
        cache.put(workspace.id, first)
        assert await cache.get(workspace.id, expected_snapshot_id=version) is first
        _skill(skills, "one", "Edited after local cache")
        second = await _skill_snapshot(workspace, task, skills, first)
        assert snapshot_id(first) != snapshot_id(second)
        version = await storage.projects.save_analysis(second, expected_snapshot_id=version)
        _skill(skills, "one", "Edited after hot cache")
        fresh_service = WorkspaceService(storage, skills_dir=str(skills))
        with pytest.raises(PermissionError, match="authorized_workspace_analysis_required"):
            await fresh_service.analyze(workspace.id)
        third = await _skill_snapshot(workspace, task, skills, second)
        assert snapshot_id(third) != snapshot_id(second)
        assert any("Edited after hot cache" in item.statement for item in third.knowledge.items)
        await storage.projects.save_analysis(third, expected_snapshot_id=version)
        assert await storage.projects.current_snapshot_id("project") == snapshot_id(third)
        without_skills = await fixture_analysis(
            WorkspaceIntelligence(), workspace, task, paths=("app.py",))
        assert not any(item.kind == "skill" for item in without_skills.knowledge.items)
        assert snapshot_id(without_skills) != snapshot_id(third)
    finally:
        await storage.close()
