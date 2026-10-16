"""Cross-module router paths are bounded static declarations, not live URLs."""

import pytest

from Sprout.workspace.models import Workspace
from Sprout.workspace.python_contracts import PythonContractAnalyzer
from Sprout.workspace.scanner import WorkspaceScanner


def _analyze(tmp_path, files):
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    workspace = Workspace(id="routes", root=tmp_path)
    return PythonContractAnalyzer().analyze(
        workspace, WorkspaceScanner().collect_resources(workspace),
    )


def _mounted(result):
    return [i for i in result.items if i.kind == "interface_mounted_endpoint"]


def test_relative_import_nested_and_multiple_mounts(tmp_path):
    result = _analyze(tmp_path, {
        "app/__init__.py": "",
        "app/users.py": '''from fastapi import APIRouter
router = APIRouter(prefix="/users")
@router.get("/{id}")
def read(id: int): pass
''',
        "app/api.py": '''from fastapi import APIRouter
from .users import router as users
router = APIRouter(prefix="/api")
router.include_router(users, prefix="/v1")
''',
        "app/main.py": '''from fastapi import FastAPI
from . import api
app = FastAPI()
app.include_router(api.router, prefix="/public")
app.include_router(api.router, prefix="/private")
''',
    })
    mounts = _mounted(result)
    assert [i.details["mounted_path"] for i in mounts] == [
        "/public/api/v1/users/{id}", "/private/api/v1/users/{id}",
    ]
    assert len(mounts[0].details["mount_chain"]) == 2
    assert len(mounts[0].evidence_ids) == 3
    assert mounts[0].details["deployed"] is False


def test_late_router_routes_do_not_guess_framework_version_semantics(tmp_path):
    result = _analyze(tmp_path, {"main.py": '''from fastapi import FastAPI, APIRouter
router = APIRouter()
app = FastAPI()
app.include_router(router, prefix="/early")
@router.get("/late")
def late(): pass
app.include_router(router, prefix="/after")
'''})
    assert not _mounted(result)
    assert any("framework-version" in i.statement for i in result.items)


def test_dynamic_or_conditional_mount_is_not_resolved(tmp_path):
    result = _analyze(tmp_path, {"main.py": '''from fastapi import FastAPI, APIRouter
router = APIRouter()
@router.get("/x")
def x(): pass
app = FastAPI()
if enabled:
    app.include_router(router, prefix="/maybe")
app.include_router(router, prefix=unknown)
'''})
    assert not _mounted(result)
    assert any(i.kind == "analysis_warning" for i in result.items)


def test_import_alias_reexport_and_source_code_not_executed(tmp_path):
    sentinel = tmp_path / "executed"
    result = _analyze(tmp_path, {
        "routes.py": f'''from fastapi import APIRouter as Router
from pathlib import Path
Path({str(sentinel)!r}).touch()
router = Router(prefix="/v1")
@router.post("/items")
def item(): pass
''',
        "exports.py": "from routes import router as exported\n",
        "main.py": '''import fastapi as f
import exports as e
app = f.FastAPI()
app.include_router(e.exported, prefix="/api")
''',
    })
    assert _mounted(result)[0].details["mounted_path"] == "/api/v1/items"
    assert not sentinel.exists()


@pytest.mark.parametrize("mutation", [
    "router = custom", "from custom import router", "configure(router)",
    "helper.configure(router)", "router.prefix = '/changed'",
    "app.include_router(router, prefix='/invalid/')",
    "app.include_router(router, **options)", "app.include_router(app)",
])
def test_shadowing_mutation_and_invalid_mounts_are_conservative(tmp_path, mutation):
    result = _analyze(tmp_path, {"main.py": f'''from fastapi import FastAPI, APIRouter
router = APIRouter()
@router.get("/x")
def x(): pass
app = FastAPI()
{mutation}
app.include_router(router, prefix="/ok")
'''})
    assert not _mounted(result)
    assert any(i.kind == "analysis_warning" for i in result.items)


def test_cycle_and_import_beyond_package_are_unresolved(tmp_path):
    result = _analyze(tmp_path, {
        "a.py": '''from b import router
from fastapi import FastAPI
app = FastAPI()
app.include_router(router)
''',
        "b.py": "from a import router\n",
        "pkg/__init__.py": "",
        "pkg/main.py": '''from fastapi import FastAPI
from ...a import router
app = FastAPI()
app.include_router(router)
''',
    })
    assert not _mounted(result)
    assert any(i.kind == "analysis_warning" for i in result.items)


def test_src_layout_absolute_import_and_unmounted_router(tmp_path):
    result = _analyze(tmp_path, {
        "src/api/users.py": '''from fastapi import APIRouter
router = APIRouter()
@router.get("/x")
def x(): pass
''',
        "src/main.py": '''from fastapi import FastAPI
import api.users
app = FastAPI()
app.include_router(api.users.router, prefix="/v1")
''',
        "unused.py": '''from fastapi import APIRouter
router = APIRouter()
@router.get("/unused")
def unused(): pass
''',
    })
    assert [i.details["mounted_path"] for i in _mounted(result)] == ["/v1/x"]


@pytest.mark.parametrize("late_mutation", [
    "router.include_router(extra)", "configure(extra)", "result = helper.configure(extra)",
])
def test_late_nested_or_unknown_mutation_invalidates_ancestor_app(tmp_path, late_mutation):
    result = _analyze(tmp_path, {"main.py": f'''from fastapi import FastAPI, APIRouter
extra = APIRouter()
@extra.get("/x")
def x(): pass
router = APIRouter()
router.include_router(extra)
app = FastAPI()
app.include_router(router)
{late_mutation}
'''})
    assert not _mounted(result)
    assert any(i.kind == "analysis_warning" for i in result.items)


def test_imported_module_attribute_assignment_invalidates_router(tmp_path):
    result = _analyze(tmp_path, {
        "routes.py": '''from fastapi import APIRouter
router = APIRouter()
@router.get("/x")
def x(): pass
''',
        "main.py": '''from fastapi import FastAPI
import routes
routes.router = custom
app = FastAPI()
app.include_router(routes.router)
''',
    })
    assert not _mounted(result)


def test_local_fastapi_module_is_not_official_constructor(tmp_path):
    result = _analyze(tmp_path, {
        "fastapi.py": "FastAPI = custom\nAPIRouter = custom\n",
        "main.py": '''from fastapi import FastAPI, APIRouter
router = APIRouter()
@router.get("/x")
def x(): pass
app = FastAPI()
app.include_router(router)
''',
    })
    assert not _mounted(result)


def test_mount_details_persist_and_edit_delete_invalidate(tmp_path):
    import asyncio
    import sqlite3

    from Sprout.storage.local.sqlite.projects import SqliteProjectAnalysisStore
    from Sprout.task.models import Task
    from Sprout.workspace.intelligence import WorkspaceIntelligence

    async def run():
        root = tmp_path / "source"
        root.mkdir()
        _analyze(root, {
            "routes.py": '''from fastapi import APIRouter
router = APIRouter(prefix="/users")
@router.get("/{id}")
def read(id: int): pass
''',
            "main.py": '''from fastapi import FastAPI
from routes import router
app = FastAPI()
app.include_router(router, prefix="/v1")
''',
        })
        workspace = Workspace(id="routes", root=root)
        task = Task(workspace_id=workspace.id, instruction="analyze")
        intelligence = WorkspaceIntelligence()
        first = await intelligence.analyze(workspace, task)
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            version = await store.save_analysis(first, expected_snapshot_id=None)
        finally:
            store.close()
        with sqlite3.connect(tmp_path / "projects/routes/project_architecture.db") as db:
            assert db.execute(
                "SELECT COUNT(*) FROM ArchitectureFact WHERE kind='interface_mounted_endpoint'",
            ).fetchone()[0] == 1
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            restored = await store.load_analysis(workspace.id)
            facts = [i for i in restored.knowledge.items if i.kind == "interface_mounted_endpoint"]
            assert facts[0].details["mounted_path"] == "/v1/users/{id}"
            assert len(facts[0].details["mount_chain"]) == 1
            path = root / "main.py"
            path.write_text(path.read_text(encoding="utf-8").replace("/v1", "/v2"),
                            encoding="utf-8")
            edited = await intelligence.analyze(workspace, task, previous=restored)
            facts = [i for i in edited.knowledge.items if i.kind == "interface_mounted_endpoint"]
            assert facts[0].details["mounted_path"] == "/v2/users/{id}"
            assert any(i.id in edited.stale_knowledge_ids for i in restored.knowledge.items
                       if i.kind == "interface_mounted_endpoint")
            version = await store.save_analysis(edited, expected_snapshot_id=version)
            (root / "routes.py").unlink()
            removed = await intelligence.analyze(workspace, task, previous=edited)
            assert not any(i.kind == "interface_mounted_endpoint" for i in removed.knowledge.items)
            assert facts[0].id in removed.stale_knowledge_ids
            await store.save_analysis(removed, expected_snapshot_id=version)
        finally:
            store.close()

    asyncio.run(run())
