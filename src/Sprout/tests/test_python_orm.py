"""Explicit declarative ORM models are parsed without importing SQLAlchemy."""

import pytest

from Sprout.runtime.workspaces import WorkspaceService
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.metadata import SqliteMetadataStore
from Sprout.storage.local.sqlite.projects import SqliteProjectAnalysisStore
from Sprout.tools.project_analyze_tool import ProjectAnalyzeTool
from Sprout.workspace.models import Workspace
from Sprout.workspace.python_contracts import PythonContractAnalyzer
from Sprout.workspace.scanner import WorkspaceScanner


def _analyze(tmp_path, source):
    (tmp_path / "models.py").write_text(source, encoding="utf-8")
    workspace = Workspace(id="project", root=tmp_path)
    return PythonContractAnalyzer().analyze(
        workspace, WorkspaceScanner().collect_resources(workspace),
    )


def test_declarative_base_mapped_columns_and_relationships(tmp_path):
    result = _analyze(tmp_path, '''
from sqlalchemy import Integer, String, ForeignKey
from sqlalchemy.orm import DeclarativeBase as DB, Mapped, mapped_column as mc, relationship
class Base(DB): pass
class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "public"}
    id: Mapped[int] = mc(Integer, primary_key=True)
    name: Mapped[str] = mc("display_name", String(80), unique=True, nullable=False)
    team_id: Mapped[int] = mc(ForeignKey("teams.id"))
    team: Mapped["Team"] = relationship("Team", back_populates="users")
''')
    table = next(item for item in result.items if item.kind == "database_table")
    assert table.details["declaration_type"] == "declarative_model"
    assert table.details["model"] == "User"
    assert table.details["table"] == "users"
    assert table.details["schema"] == "public"
    columns = table.details["columns"]
    assert [column["name"] for column in columns] == ["id", "display_name", "team_id"]
    assert columns[0]["primary_key"] is True
    assert columns[0]["annotation_expression"] == "Mapped[int]"
    assert columns[1]["attribute"] == "name"
    assert columns[1]["nullable"] is False
    assert columns[2]["foreign_key_targets"] == ["teams.id"]
    assert columns[2]["nullable"] is None
    relation = table.details["relationships"][0]
    assert relation["attribute"] == "team"
    assert relation["target"] == "Team"
    assert relation["back_populates"] == "users"
    assert relation["target_resolved"] is False
    assert table.details["inherited_elements_resolved"] is False
    assert table.details["complete"] is False
    assert table.details["line"] == 5


@pytest.mark.parametrize("factory", [
    "sqlalchemy.orm.declarative_base", "sqlalchemy.ext.declarative.declarative_base",
])
def test_legacy_factory_base_and_column_names(tmp_path, factory):
    module, _, name = factory.rpartition(".")
    result = _analyze(tmp_path, f'''
from {module} import {name} as factory
from sqlalchemy import Column, Integer, String
Base = factory()
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    label = Column(name="display_name", type_=String(50), nullable=False)
''')
    table = next(item for item in result.items if item.kind == "database_table")
    assert [column["name"] for column in table.details["columns"]] == ["id", "display_name"]
    assert table.details["columns"][1]["type_expression"] == "String(50)"


def test_annotation_only_columns_keep_type_and_nullability_unknown(tmp_path):
    result = _analyze(tmp_path, '''
import sqlalchemy.orm as orm
class Base(orm.DeclarativeBase): pass
class User(Base):
    __tablename__ = "users"
    id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    value: orm.Mapped[str | None]
    not_a_column: str
''')
    table = next(item for item in result.items if item.kind == "database_table")
    assert len(table.details["columns"]) == 2
    column = table.details["columns"][1]
    assert column["name"] == "value"
    assert column["type_expression"] is None
    assert column["nullable"] is None
    assert column["annotation_expression"] == "orm.Mapped[str | None]"


def test_fake_base_rebinding_and_unknown_cross_file_bases_are_not_guessed(tmp_path):
    result = _analyze(tmp_path, '''
from custom import DeclarativeBase
from sqlalchemy import Column, Integer
class FakeBase(DeclarativeBase): pass
class Fake(FakeBase):
    __tablename__ = "fake"
    id = Column(Integer)
from sqlalchemy.orm import declarative_base
Base = declarative_base()
Base = custom_base
class Reassigned(Base):
    __tablename__ = "reassigned"
    id = Column(Integer)
from .base import Base as ImportedBase
class External(ImportedBase):
    __tablename__ = "external"
    id = Column(Integer)
''')
    assert not any(item.kind == "database_table" for item in result.items)


def test_dynamic_table_names_and_constraints_are_not_reported_as_complete(tmp_path):
    result = _analyze(tmp_path, '''
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy import Integer, UniqueConstraint
class Base(DeclarativeBase): pass
class Dynamic(Base):
    __tablename__ = configured_name
    id = mapped_column(Integer)
class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("id"), {"schema": "public"})
    id = mapped_column(Integer)
''')
    assert any(item.kind == "analysis_warning" for item in result.items)
    tables = [item for item in result.items if item.kind == "database_table"]
    assert len(tables) == 1
    assert tables[0].details["schema"] == "public"
    assert tables[0].details["complete"] is False
    assert tables[0].details["unresolved_elements"]


def test_overwritten_and_class_shadowed_columns_do_not_leak(tmp_path):
    result = _analyze(tmp_path, '''
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = "users"
    id = Column(Integer)
    id = "not a column"
    Column = custom_column
    name = Column(Integer)
''')
    table = next(item for item in result.items if item.kind == "database_table")
    assert table.details["columns"] == []
    assert table.details["complete"] is False


async def test_orm_analysis_tool_exposes_details_without_running_model_code(tmp_path):
    sentinel = tmp_path / "executed.txt"
    _analyze(tmp_path, f'''
from pathlib import Path
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy import Integer
Path({str(sentinel)!r}).write_text("must not run")
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = "users"
    id = mapped_column(Integer, primary_key=True)
''')
    result = await ProjectAnalyzeTool().invoke({"path": str(tmp_path)})
    table = next(item for item in result.data["knowledge"] if item["kind"] == "database_table")
    assert table["details"]["model"] == "User"
    assert not sentinel.exists()


def test_annotation_without_value_preserves_explicit_column(tmp_path):
    result = _analyze(tmp_path, '''
from sqlalchemy.orm import DeclarativeBase, Mapped
from sqlalchemy import Column, Integer
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    id: Mapped[int]
''')
    table = next(item for item in result.items if item.kind == "database_table")
    column = table.details["columns"][0]
    assert column["primary_key"] is True
    assert column["type_expression"] == "Integer"
    assert column["annotation_expression"] == "Mapped[int]"


def test_conditional_base_and_dynamic_class_metadata_are_not_guessed(tmp_path):
    result = _analyze(tmp_path, '''
from sqlalchemy.orm import DeclarativeBase
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = "users"
    if condition:
        __tablename__ = "different"
if condition:
    Base = custom_base
class Other(Base):
    __tablename__ = "other"
''')
    assert not any(item.kind == "database_table" for item in result.items)
    assert any(item.kind == "analysis_warning" for item in result.items)


def test_abstract_base_is_not_a_table(tmp_path):
    result = _analyze(tmp_path, '''
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy import Integer
class Base(DeclarativeBase): pass
class Abstract(Base):
    __abstract__ = True
    id = mapped_column(Integer, primary_key=True)
class Concrete(Abstract):
    __tablename__ = "concrete"
    value = mapped_column(Integer)
''')
    tables = [item for item in result.items if item.kind == "database_table"]
    assert len(tables) == 1
    assert tables[0].details["table"] == "concrete"
    assert [column["name"] for column in tables[0].details["columns"]] == ["value"]
    assert tables[0].details["inherited_elements_resolved"] is False


def test_annotated_configuration_does_not_invent_negative_flags(tmp_path):
    result = _analyze(tmp_path, '''
from typing import Annotated
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
class Base(DeclarativeBase): pass
IdType = Annotated[int, mapped_column(primary_key=True, unique=True)]
class User(Base):
    __tablename__ = "users"
    id: Mapped[IdType]
''')
    table = next(item for item in result.items if item.kind == "database_table")
    column = table.details["columns"][0]
    assert column["primary_key"] is None
    assert column["unique"] is None
    assert column["index"] is None
    assert column["annotation_configuration_resolved"] is False


def test_conditional_import_shadowing_invalidates_local_orm_base(tmp_path):
    result = _analyze(tmp_path, '''
from sqlalchemy.orm import DeclarativeBase
class Base(DeclarativeBase): pass
if condition:
    from custom import Base
class User(Base):
    __tablename__ = "users"
''')
    assert not any(item.kind == "database_table" for item in result.items)


async def test_orm_authority_restart_cache_refresh_and_deleted_model(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    _analyze(root, '''
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy import Integer
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = "users"
    id = mapped_column(Integer, primary_key=True)
''')
    storage = StorageBundle.in_memory()
    storage.metadata = SqliteMetadataStore(SqliteDatabase.open(tmp_path / "runtime.db"))
    storage.projects = SqliteProjectAnalysisStore(tmp_path / "projects")
    try:
        service = WorkspaceService(storage)
        workspace = await service.open(root, workspace_id="project")
        first = await service.analyze(workspace.id)
        original = next(item for item in first.knowledge.items if item.kind == "database_table")
        storage.projects.close()
        storage.projects = SqliteProjectAnalysisStore(tmp_path / "projects")
        restored = await storage.projects.load_analysis("project")
        assert next(item for item in restored.knowledge.items
                    if item.kind == "database_table") == original
        source = root / "models.py"
        source.write_text(source.read_text(encoding="utf-8")
                          + '    team_id = mapped_column(Integer, nullable=False)\n',
                          encoding="utf-8")
        changed = await service.analyze("project")
        current = next(item for item in changed.knowledge.items if item.kind == "database_table")
        assert [column["name"] for column in current.details["columns"]] == ["id", "team_id"]
        stored = await storage.projects.load_analysis("project")
        assert next(item for item in stored.knowledge.items if item.kind == "database_table") == (
            current
        )
        source.unlink()
        deleted = await WorkspaceService(storage).analyze("project")
        assert not any(item.kind == "database_table" for item in deleted.knowledge.items)
        assert current.id in deleted.stale_knowledge_ids
        final = await storage.projects.load_analysis("project")
        assert not any(item.kind == "database_table" for item in final.knowledge.items)
    finally:
        await storage.close()
