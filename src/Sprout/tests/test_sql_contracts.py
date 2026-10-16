"""SQL declarations are source evidence, never executable migrations."""

import pytest

from Sprout.workspace.models import Workspace
from Sprout.workspace.scanner import WorkspaceScanner
from Sprout.workspace.sql_contracts import SqlContractAnalyzer


def _analyze(tmp_path, source):
    (tmp_path / "schema.sql").write_text(source, encoding="utf-8")
    workspace = Workspace(id="sql-project", root=tmp_path)
    return SqlContractAnalyzer().analyze(workspace, WorkspaceScanner().collect_resources(workspace))


def test_nested_expressions_quotes_comments_and_composite_constraints(tmp_path):
    items = _analyze(tmp_path, '''-- CREATE TABLE fake(x);
CREATE /* comment */ TABLE IF NOT EXISTS "main"."a.b" (
 "id" INTEGER NOT NULL,
 value NUMERIC(10, 2) DEFAULT 'a,b;CREATE TABLE fake(x)',
 CONSTRAINT pk PRIMARY KEY (id, value),
 CHECK (value IN ('x,y', 'z'))
) STRICT;
''')
    assert len(items) == 1
    details = items[0].details
    assert details["table"] == "a.b"
    assert details["qualifiers"] == ["main"]
    assert [c["name"] for c in details["columns"]] == ["id", "value"]
    assert len(details["constraint_expressions"]) == 2
    assert details["options_expression"] == "STRICT"
    assert details["complete"] is False
    assert details["migration_state_resolved"] is False
    assert items[0].details["line"] == 2


def test_dollar_quoted_function_body_does_not_create_fake_tables(tmp_path):
    items = _analyze(tmp_path,
        "CREATE FUNCTION f() RETURNS void AS $body$ CREATE TABLE fake(x); $body$; "
        "CREATE TABLE real(id int);",
    )
    assert [i.details["table"] for i in items if i.kind == "database_table"] == ["real"]
    assert any(i.kind == "analysis_warning" for i in items)


@pytest.mark.parametrize("source", [
    "CREATE TABLE x(id int", "CREATE TABLE x(id int DEFAULT 'broken);",
    "CREATE TABLE x(id int); /* broken", "CREATE TABLE x AS SELECT 1;",
    "CREATE TABLE x();", "CREATE TABLE", "ALTER TABLE x ADD COLUMN y int;",
])
def test_incomplete_or_unsupported_sql_has_warning(tmp_path, source):
    items = _analyze(tmp_path, source)
    assert items and all(i.kind == "analysis_warning" for i in items)


def test_backtick_bracket_identifiers_and_multiple_tables(tmp_path):
    items = _analyze(tmp_path, "CREATE TABLE `a``b` (`x` INT); CREATE TABLE [second] ([y] TEXT);")
    assert [i.details["table"] for i in items] == ["a`b", "second"]


def test_sql_is_never_executed(tmp_path, monkeypatch):
    import sqlite3

    def forbidden(*args, **kwargs):
        pytest.fail("SQL source must not open a database")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    items = _analyze(tmp_path,
        "ATTACH DATABASE 'external.db' AS x; CREATE TABLE demo(id INT); DROP TABLE demo;",
    )
    assert any(i.kind == "database_table" for i in items)
    assert not (tmp_path / "external.db").exists()


def test_sql_snapshot_restart_edit_and_delete(tmp_path):
    import asyncio

    from Sprout.storage.local.sqlite.projects import SqliteProjectAnalysisStore
    from Sprout.task.models import Task
    from Sprout.workspace.intelligence import WorkspaceIntelligence

    async def run():
        root = tmp_path / "source"
        root.mkdir()
        path = root / "schema.sql"
        path.write_text("CREATE TABLE demo(id INT);", encoding="utf-8")
        workspace = Workspace(id="sql-project", root=root)
        task = Task(workspace_id=workspace.id, instruction="analyze")
        intelligence = WorkspaceIntelligence()
        first = await intelligence.analyze(workspace, task)
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            version = await store.save_analysis(first, expected_snapshot_id=None)
        finally:
            store.close()
        store = SqliteProjectAnalysisStore(tmp_path / "projects")
        try:
            restored = await store.load_analysis(workspace.id)
            facts = [i for i in restored.knowledge.items if i.kind == "database_table"]
            assert facts[0].details["columns"][0]["name"] == "id"
            path.write_text("CREATE TABLE demo(id INT, name TEXT);", encoding="utf-8")
            edited = await intelligence.analyze(workspace, task, previous=restored)
            facts = [i for i in edited.knowledge.items if i.kind == "database_table"]
            assert len(facts[0].details["columns"]) == 2
            version = await store.save_analysis(edited, expected_snapshot_id=version)
            path.unlink()
            removed = await intelligence.analyze(workspace, task, previous=edited)
            assert not any(i.kind == "database_table" for i in removed.knowledge.items)
            assert facts[0].id in removed.stale_knowledge_ids
            await store.save_analysis(removed, expected_snapshot_id=version)
        finally:
            store.close()

    asyncio.run(run())
