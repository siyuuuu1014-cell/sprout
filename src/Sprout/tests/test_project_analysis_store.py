"""Project-owned authorities survive restart without losing analysis evidence."""

import asyncio
import multiprocessing
from dataclasses import replace

import pytest

from Sprout.storage.contracts.projects import ProjectSnapshotConflict
from Sprout.storage.local.sqlite.projects import SqliteProjectAnalysisStore
from Sprout.task.models import Task
from Sprout.workspace.intelligence import WorkspaceIntelligence
from Sprout.workspace.invalidation import snapshot_id
from Sprout.workspace.models import Workspace
from Sprout.workspace.serialization import to_jsonable


def _concurrent_project_writer(root, payload, barrier, results):
    from Sprout.storage.local.sqlite.driver import SqliteDatabase
    from Sprout.workspace.serialization import from_jsonable

    async def run():
        execute = SqliteDatabase.execute

        async def synchronized_publish(db, sql, parameters=()):
            if sql.startswith("INSERT OR REPLACE INTO AnalysisManifest"):
                # Both writers must finish their partitions before either publishes.
                # This exercises the final CAS, not just the early baseline check.
                barrier.wait(timeout=15)
            return await execute(db, sql, parameters)

        SqliteDatabase.execute = synchronized_publish
        store = SqliteProjectAnalysisStore(root)
        try:
            from Sprout.workspace.intelligence import WorkspaceAnalysis

            analysis = from_jsonable(payload, WorkspaceAnalysis)
            store._databases(analysis.workspace.id)
            barrier.wait(timeout=15)
            await store.save_analysis(analysis, expected_snapshot_id=None)
            results.put(None)
        except ProjectSnapshotConflict:
            results.put("conflict")
        except Exception as exc:
            results.put(f"{type(exc).__name__}: {exc}")
        finally:
            store.close()
            SqliteDatabase.execute = execute

    asyncio.run(run())


def test_concurrent_processes_publish_one_complete_snapshot(tmp_path):
    async def build():
        root = tmp_path / "source"
        root.mkdir()
        (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        workspace = Workspace(id="concurrent", root=root)
        return await WorkspaceIntelligence().analyze(
            workspace, Task(workspace_id=workspace.id, instruction="analyze"),
        )

    first = asyncio.run(build())
    fact = first.knowledge.items[0]
    second = replace(first, knowledge=replace(first.knowledge, items=(
        replace(fact, statement="Alternate analyzed fact"), *first.knowledge.items[1:],
    )))
    root = tmp_path / "projects"
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    workers = [context.Process(target=_concurrent_project_writer,
                               args=(root, to_jsonable(item), barrier, results))
               for item in (first, second)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=25)
            assert not worker.is_alive() and worker.exitcode == 0
        outcomes = [results.get(timeout=5), results.get(timeout=5)]
        assert outcomes.count(None) == 1 and outcomes.count("conflict") == 1, outcomes
        store = SqliteProjectAnalysisStore(root)
        try:
            loaded = asyncio.run(store.load_analysis("concurrent"))
            assert to_jsonable(loaded) in (to_jsonable(first), to_jsonable(second))
        finally:
            store.close()
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        results.close()


@pytest.mark.parametrize("backend", ["sqlite", "memory"])
def test_stale_analysis_cannot_replace_a_newer_published_snapshot(tmp_path, backend):
    from Sprout.storage.local.memory.projects import MemoryProjectAnalysisStore

    async def run():
        root = tmp_path / "source"
        root.mkdir()
        (root / "app.py").write_text("value = 1\n", encoding="utf-8")
        workspace = Workspace(id="project", root=root)
        first = await WorkspaceIntelligence().analyze(workspace, Task(instruction="analyze"))
        fact = first.knowledge.items[0]
        newer = replace(first, knowledge=replace(first.knowledge, items=(
            replace(fact, statement="New project fact"), *first.knowledge.items[1:],
        )))
        stale = replace(first, knowledge=replace(first.knowledge, items=(
            replace(fact, statement="Stale project fact"), *first.knowledge.items[1:],
        )))
        store = (SqliteProjectAnalysisStore(tmp_path / "projects") if backend == "sqlite"
                 else MemoryProjectAnalysisStore())
        try:
            initial = await store.save_analysis(first, expected_snapshot_id=None)
            published = await store.save_analysis(newer, expected_snapshot_id=initial)
            with pytest.raises(ProjectSnapshotConflict, match="retry"):
                await store.save_analysis(stale, expected_snapshot_id=initial)
            assert await store.current_snapshot_id(workspace.id) == published
            assert to_jsonable(await store.load_analysis(workspace.id)) == to_jsonable(newer)
        finally:
            store.close()

    asyncio.run(run())


def test_project_snapshot_roundtrip_and_current_graph(tmp_path):
    async def run():
        root = tmp_path / "source"
        root.mkdir()
        source = root / "app.py"
        source.write_text("def run():\n    return 1\n", encoding="utf-8")
        workspace = Workspace(id="project-1", root=root)
        task = Task(workspace_id=workspace.id, instruction="understand project")
        intelligence = WorkspaceIntelligence()
        analysis = await intelligence.analyze(workspace, task)
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        version = await store.save_analysis(analysis, expected_snapshot_id=None)
        assert await store.save_analysis(analysis, expected_snapshot_id=version) == version
        store.close()
        reopened = SqliteProjectAnalysisStore(tmp_path / "projects")
        loaded = await reopened.load_analysis(workspace.id)
        assert to_jsonable(loaded) == to_jsonable(analysis)
        assert len(list((tmp_path / "projects" / workspace.id).glob("project_*.db"))) == 5
        source.unlink()
        replacement = root / "other.py"
        replacement.write_text("def other():\n    return 2\n", encoding="utf-8")
        changed = await intelligence.analyze(workspace, task, previous=loaded)
        assert await reopened.save_analysis(changed, expected_snapshot_id=version) != version
        current = await reopened.load_analysis(workspace.id)
        assert all(node.resource is None or not node.resource.path.endswith("app.py")
                   for node in current.graph.nodes)
        reopened.close()

    asyncio.run(run())


def test_failed_partition_keeps_published_snapshot_and_retry_recovers(tmp_path, monkeypatch):
    from Sprout.storage.local.sqlite.driver import SqliteDatabase

    async def run():
        root = tmp_path / "source"
        root.mkdir()
        source = root / "app.py"
        source.write_text("def run():\n    return 1\n", encoding="utf-8")
        workspace = Workspace(id="project", root=root)
        task = Task(workspace_id=workspace.id, instruction="analyze")
        intelligence = WorkspaceIntelligence()
        first = await intelligence.analyze(workspace, task)
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        await store.save_analysis(first, expected_snapshot_id=None)
        source.write_text("def run():\n    return 2\n", encoding="utf-8")
        changed = await intelligence.analyze(workspace, task, previous=first)
        failing = store._databases(workspace.id)["architecture"]
        execute_batch = SqliteDatabase.execute_batch

        async def injected(db, statements):
            if db is failing:
                raise RuntimeError("Injected partition failure")
            return await execute_batch(db, statements)

        with monkeypatch.context() as patch:
            patch.setattr(SqliteDatabase, "execute_batch", injected)
            with pytest.raises(RuntimeError, match="Injected partition"):
                await store.save_analysis(changed, expected_snapshot_id=snapshot_id(first))
            assert to_jsonable(await store.load_analysis(workspace.id)) == to_jsonable(first)
        store.close()
        reopened = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            assert to_jsonable(await reopened.load_analysis(workspace.id)) == to_jsonable(first)
            await reopened.save_analysis(changed, expected_snapshot_id=snapshot_id(first))
            assert to_jsonable(await reopened.load_analysis(workspace.id)) == to_jsonable(changed)
        finally:
            reopened.close()

    asyncio.run(run())
