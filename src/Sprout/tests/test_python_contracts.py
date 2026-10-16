"""Static declarations keep structured evidence through project authority restart."""

import asyncio
import sqlite3

from Sprout.storage.local.sqlite.projects import SqliteProjectAnalysisStore
from Sprout.task.models import Task
from Sprout.tools.project_analyze_tool import ProjectAnalyzeTool
from Sprout.workspace.intelligence import WorkspaceIntelligence
from Sprout.workspace.models import Workspace
from Sprout.workspace.python_contracts import PythonContractAnalyzer
from Sprout.workspace.scanner import WorkspaceScanner


def _analyze(tmp_path, source):
    path = tmp_path / "app.py"
    path.write_text(source, encoding="utf-8")
    workspace = Workspace(id="project", root=tmp_path)
    resources = WorkspaceScanner().collect_resources(workspace)
    return PythonContractAnalyzer().analyze(workspace, resources)


def test_fastapi_declared_routes_preserve_handler_shapes_and_source(tmp_path):
    result = _analyze(tmp_path, '''
from fastapi import APIRouter as Router
router = Router(prefix="/v1")
@router.get("/items/{id}", response_model=Item, status_code=200)
async def read_item(id: int, *, detail: bool = False) -> Item:
    return Item()
@router.api_route("/items", methods=["POST", "PUT"])
def create_item(payload: Payload):
    return payload
''')
    endpoints = [item for item in result.items if item.kind == "interface_endpoint"]
    assert len(endpoints) == 3
    details = endpoints[0].details
    assert details["declared_path"] == "/v1/items/{id}"
    assert details["method"] == "GET"
    assert details["handler"] == "read_item"
    assert details["return_annotation"] == "Item"
    assert details["response_model_expression"] == "Item"
    assert details["status_code"] == 200
    assert details["parameters"] == [
        {"name": "id", "annotation_expression": "int", "default_expression": None},
        {"name": "detail", "annotation_expression": "bool", "default_expression": "False"},
    ]
    assert details["mount_path_resolved"] is False
    assert details["line"] == 4
    assert endpoints[0].evidence_ids == ((tmp_path / "app.py").resolve().as_posix(),)


def test_sqlalchemy_core_columns_preserve_unknowns_constraints_and_foreign_keys(tmp_path):
    result = _analyze(tmp_path, '''
import sqlalchemy as sa
users = sa.Table("users", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("team_id", sa.Integer, sa.ForeignKey("teams.id"), nullable=False),
    sa.Column("name", sa.String(80), unique=True, nullable=setting, server_default="guest"),
    sa.UniqueConstraint("team_id", "name"), schema="public")
''')
    table = next(item for item in result.items if item.kind == "database_table")
    assert table.details["table"] == "users"
    assert table.details["schema"] == "public"
    columns = table.details["columns"]
    assert len(columns) == 3
    assert columns[0]["primary_key"] is True
    assert columns[0]["nullable"] is False
    assert columns[1]["foreign_key_targets"] == ["teams.id"]
    assert columns[2]["nullable"] is None
    assert columns[2]["type_expression"] == "sa.String(80)"
    assert table.details["complete"] is False
    assert table.details["unresolved_elements"] == ["sa.UniqueConstraint('team_id', 'name')"]


def test_dynamic_declarations_are_reported_not_guessed(tmp_path):
    result = _analyze(tmp_path, '''
from fastapi import FastAPI
from sqlalchemy import Table
app = FastAPI()
@app.get(route_from_config)
def handler(): pass
table = Table(get_table_name(), metadata)
''')
    assert {item.kind for item in result.items} == {"analysis_warning"}
    assert len(result.items) == 2


def test_unrelated_names_and_reassigned_receivers_are_not_frameworks(tmp_path):
    result = _analyze(tmp_path, '''
from custom import Table, FastAPI
table = Table("fake", metadata)
app = FastAPI()
@app.get("/fake")
def handler(): pass
from fastapi import FastAPI as RealApp
other = RealApp()
other = custom_router
@other.post("/fake")
def other_handler(): pass
''')
    assert not result.items


def test_project_code_is_never_imported_or_executed(tmp_path):
    sentinel = tmp_path / "executed.txt"
    result = _analyze(tmp_path, f'''
from pathlib import Path
from fastapi import FastAPI
Path({str(sentinel)!r}).write_text("must not execute")
raise RuntimeError("must not execute")
app = FastAPI()
@app.get("/safe")
def handler(): pass
''')
    assert len(result.items) == 1
    assert not sentinel.exists()


def test_contract_details_survive_sqlite_restart_and_source_deletion(tmp_path):
    async def run():
        root = tmp_path / "source"
        root.mkdir()
        _analyze(root, '''
from fastapi import FastAPI
from sqlalchemy import Table, Column, Integer
app = FastAPI()
@app.get("/users")
def users(): pass
table = Table("users", metadata, Column("id", Integer, primary_key=True))
''')
        workspace = Workspace(id="project", root=root)
        task = Task(workspace_id=workspace.id, instruction="analyze")
        intelligence = WorkspaceIntelligence()
        first = await intelligence.analyze(workspace, task)
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            version = await store.save_analysis(first, expected_snapshot_id=None)
        finally:
            store.close()
        with sqlite3.connect(tmp_path / "projects/project/project_database.db") as db:
            count = db.execute(
                "SELECT COUNT(*) FROM DatabaseFact WHERE kind='database_table'",
            ).fetchone()[0]
            assert count == 1
        with sqlite3.connect(tmp_path / "projects/project/project_architecture.db") as db:
            count = db.execute(
                "SELECT COUNT(*) FROM ArchitectureFact WHERE kind='interface_endpoint'",
            ).fetchone()[0]
            assert count == 1
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            restored = await store.load_analysis("project")
            expected = [item for item in first.knowledge.items
                        if item.kind in {"interface_endpoint", "database_table"}]
            actual = [item for item in restored.knowledge.items
                      if item.kind in {"interface_endpoint", "database_table"}]
            assert actual == expected
            (root / "app.py").unlink()
            removed = await intelligence.analyze(workspace, task, previous=restored)
            assert not any(item.kind in {"interface_endpoint", "database_table"}
                           for item in removed.knowledge.items)
            assert all(item.id in removed.stale_knowledge_ids for item in expected)
            await store.save_analysis(removed, expected_snapshot_id=version)
        finally:
            store.close()

    asyncio.run(run())


def test_reimported_and_conditionally_reassigned_receivers_are_not_guessed(tmp_path):
    result = _analyze(tmp_path, '''
from fastapi import FastAPI
app = FastAPI()
from custom import app
@app.get("/fake")
def first(): pass
other = FastAPI()
if condition:
    other = custom_router
@other.get("/uncertain")
def second(): pass
''')
    assert not result.items


async def test_standalone_analysis_tool_exposes_structured_contracts(tmp_path):
    _analyze(tmp_path, '''
from fastapi import FastAPI
app = FastAPI()
@app.get("/items")
def items(id: int): pass
''')
    result = await ProjectAnalyzeTool().invoke({"path": str(tmp_path)})
    endpoint = next(item for item in result.data["knowledge"]
                    if item["kind"] == "interface_endpoint")
    assert endpoint["details"]["declared_path"] == "/items"
    assert endpoint["details"]["parameters"][0]["name"] == "id"
