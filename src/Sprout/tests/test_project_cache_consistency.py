"""Delayed derived writes cannot change which authority snapshot readers use."""

from dataclasses import replace

import pytest

from Sprout.runtime.workspaces import WorkspaceService
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.memory import MemoryCacheStore
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.metadata import SqliteMetadataStore
from Sprout.task.models import Task
from Sprout.workspace.cache import WorkspaceAnalysisCache
from Sprout.workspace.intelligence import WorkspaceIntelligence
from Sprout.workspace.invalidation import snapshot_id
from Sprout.workspace.models import Workspace
from Sprout.workspace.serialization import to_jsonable


async def analyses(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    workspace = Workspace(id="project", root=source)
    first = await WorkspaceIntelligence().analyze(workspace, Task(instruction="analyze"))
    fact = first.knowledge.items[0]
    second = replace(first, knowledge=replace(first.knowledge, items=(
        replace(fact, statement="New published fact"), *first.knowledge.items[1:],
    )))
    return first, second


async def test_delayed_old_mirror_cannot_replace_current_version(tmp_path):
    first, second = await analyses(tmp_path)
    hot = MemoryCacheStore()
    old_writer = WorkspaceAnalysisCache(cache_store=hot)
    new_writer = WorkspaceAnalysisCache(cache_store=hot)
    await new_writer.persist("project", second)
    await old_writer.persist("project", first)
    old_writer.put("project", first)
    for reader in (old_writer, WorkspaceAnalysisCache(cache_store=hot)):
        restored = await reader.get("project", expected_snapshot_id=snapshot_id(second))
        assert to_jsonable(restored) == to_jsonable(second)
    assert all(key.startswith("project:project:analysis:") for key in hot.values)
    assert await old_writer.get("project", expected_snapshot_id=None) is None


async def test_wrong_identity_or_legacy_hot_payload_is_a_miss(tmp_path):
    first, second = await analyses(tmp_path)
    hot = MemoryCacheStore()
    version = snapshot_id(second)
    cache = WorkspaceAnalysisCache(cache_store=hot)
    key = cache._key("project", version)
    await hot.set(key, {"snapshot_id": version, "analysis": to_jsonable(first)})
    assert await cache.get("project", expected_snapshot_id=version) is None
    await hot.set(key, to_jsonable(second))
    assert await cache.get("project", expected_snapshot_id=version) is None
    altered_graph = replace(second, graph=replace(second.graph, nodes=()))
    await hot.set(key, {"snapshot_id": version, "analysis": to_jsonable(altered_graph)})
    assert await cache.get("project", expected_snapshot_id=version) is None
    with pytest.raises(ValueError, match="identity"):
        cache.put("other-project", second)


async def test_hot_cache_failure_cannot_block_project_authority(tmp_path):
    class BrokenCache(MemoryCacheStore):
        async def get(self, key):
            raise ConnectionError("cache unavailable")

        async def set(self, key, value, ttl_seconds=None):
            raise ConnectionError("cache unavailable")

    first, _ = await analyses(tmp_path)
    storage = StorageBundle.in_memory()
    storage.metadata = SqliteMetadataStore(SqliteDatabase.open(tmp_path / "metadata.db"))
    storage.cache = BrokenCache()
    await storage.metadata.save_workspace(first.workspace)
    await storage.projects.save_analysis(first, expected_snapshot_id=None)
    service = WorkspaceService(storage)
    try:
        current = await service.analyze("project")
        assert snapshot_id(current) == snapshot_id(first)
        assert await storage.projects.current_snapshot_id("project") == snapshot_id(first)
    finally:
        await storage.close()


async def test_version_mirrors_have_ttl_and_invalidate_known_versions(tmp_path):
    class RecordingCache(MemoryCacheStore):
        ttl = []

        async def set(self, key, value, ttl_seconds=None):
            self.ttl.append(ttl_seconds)
            await super().set(key, value, ttl_seconds)

    first, second = await analyses(tmp_path)
    hot = RecordingCache()
    cache = WorkspaceAnalysisCache(cache_store=hot)
    for analysis in (first, second):
        cache.put("project", analysis)
        await cache.persist("project", analysis)
    assert hot.ttl == [3600, 3600]
    await cache.forget()
    assert not hot.values
    assert await cache.get("project", expected_snapshot_id=snapshot_id(second)) is None
