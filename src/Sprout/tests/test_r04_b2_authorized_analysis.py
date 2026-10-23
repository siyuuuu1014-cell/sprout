"""Pure B2 analysis: synthetic contents are not file access credentials."""

import asyncio
from dataclasses import FrozenInstanceError, replace
from importlib import import_module, util
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.task.models import Task
from Sprout.workspace.models import ResourceKind, Workspace, WorkspaceManifest


def test_authorized_view_exists():
    assert util.find_spec("Sprout.workspace.authorized_content") is not None


@pytest.mark.parametrize("module,cls,method", [
    ("scanner", "WorkspaceScanner", "scan_view"),
    ("invalidation", "WorkspaceFingerprint", "compute_view"),
    ("graph", "WorkspaceGraphBuilder", "build_view"),
    ("knowledge", "ProjectKnowledgeBuilder", "build_view"),
    ("python_contracts", "PythonContractAnalyzer", "analyze_view"),
    ("sql_contracts", "SqlContractAnalyzer", "analyze_view"),
    ("intelligence", "WorkspaceIntelligence", "analyze_view"),
])
def test_restricted_analyzer_interface(module, cls, method):
    assert callable(getattr(getattr(import_module("Sprout.workspace." + module), cls),
                            method, None))


def content_view(files=None, **kwargs):
    from Sprout.workspace.authorized_content import AuthorizedContentView, AuthorizedEntry

    files = files if files is not None else {
        "src/main.py": b"from fastapi import FastAPI\nfrom src.worker import work, router\n"
                       b"app = FastAPI()\napp.include_router(router, prefix='/api')\n"
                       b"@app.get('/visible')\n"
                       b"def endpoint():\n    return work()\n",
        "src/worker.py": b"from fastapi import APIRouter\nrouter = APIRouter()\n"
                         b"@router.get('/work')\ndef work():\n    return 'captured'\n",
        "src/schema.sql": b"CREATE TABLE visible (id INTEGER, name TEXT);",
        "pyproject.toml": b"[project]\nname='captured-project'\n",
        "src/components/Card.tsx": b"export function Card() { return null; }",
        "src/pages/Home.tsx": b"import { Card } from '../components/Card';\n"
                               b"export function Home() { return Card(); }",
    }
    entries = tuple(AuthorizedEntry.synthetic(path, data,
                    ResourceKind.CONFIG if path.endswith(".toml") else ResourceKind.SOURCE)
                    for path, data in files.items())
    return AuthorizedContentView("w", entries, **kwargs)


@pytest.mark.parametrize("path", ["../escape.py", "/root.py", "C:/a.py", "a\\b.py",
                                  "a//b.py", "./a.py", "a/../b.py", "", "a\0.py"])
def test_invalid_relative_labels(path):
    from Sprout.workspace.authorized_content import AuthorizedEntry

    with pytest.raises(ValueError):
        AuthorizedEntry.synthetic(path, b"x")


@pytest.mark.parametrize("kind", [ResourceKind.SECRET, ResourceKind.SENSITIVE, ResourceKind.DATA,
                                  ResourceKind.EXTERNAL, ResourceKind.OTHER, ResourceKind.BINARY])
def test_unsafe_kinds_refused(kind):
    from Sprout.workspace.authorized_content import AuthorizedEntry

    with pytest.raises(ValueError):
        AuthorizedEntry.synthetic("src/visible.py", b"x", kind)


@pytest.mark.parametrize("path", [".env", "src/credentials.json", "data/table.db"])
def test_declared_source_cannot_hide_classified_content(path):
    from Sprout.workspace.authorized_content import AuthorizedEntry

    with pytest.raises(ValueError):
        AuthorizedEntry.synthetic(path, b"x", ResourceKind.SOURCE)


def test_deep_immutable_and_case_collisions():
    from Sprout.workspace.authorized_content import AuthorizedContentView

    view = content_view({"src/a.py": b"x"})
    with pytest.raises(FrozenInstanceError):
        view.entries[0].data = b"changed"
    with pytest.raises(FrozenInstanceError):
        view.entries = ()
    with pytest.raises(ValueError):
        AuthorizedContentView("w", (view.entries[0], replace(view.entries[0],
                                                            relative_path="SRC/A.py")))
    with pytest.raises(KeyError):
        view.get("src2/a.py")
    with pytest.raises(ValueError):
        AuthorizedContentView("w", view.entries, revision={"mutable": True})


@pytest.mark.parametrize("limits", [
    {"max_files": 1}, {"max_depth": 1}, {"max_file_bytes": 1}, {"max_total_bytes": 1},
])
def test_all_view_budgets(limits):
    from Sprout.workspace.authorized_content import ContentLimits

    with pytest.raises(ValueError):
        content_view(limits=ContentLimits(**limits))


async def test_full_pipeline_no_host_reads_or_store_lanes(tmp_path, monkeypatch):
    import builtins
    import os

    from Sprout.workspace.intelligence import WorkspaceIntelligence

    view = content_view()
    intelligence = WorkspaceIntelligence(skills_dir="unavailable-skills",
                                         knowledge_store=Mock(), graph_store=Mock(),
                                         vector_store=Mock())
    for name in ("_persist_knowledge", "_persist_graph"):
        monkeypatch.setattr(intelligence, name, AsyncMock(side_effect=AssertionError("persist")))
    workspace = Workspace("w", tmp_path, manifest=WorkspaceManifest(
        "w", detected_languages=("hidden-language",), entry_points=("src2/hidden.py",)))
    task = Task(workspace_id="w", instruction="understand captured selection")
    forbidden = Mock(side_effect=AssertionError("host I/O outside view"))
    with monkeypatch.context() as patches:
        for name in ("read_text", "read_bytes", "open", "rglob", "glob", "iterdir", "stat",
                     "exists", "is_file", "is_dir", "resolve"):
            patches.setattr(Path, name, forbidden)
        for owner, names in ((os, ("open", "stat", "listdir", "scandir")),
                             (builtins, ("open",))):
            for name in names:
                patches.setattr(owner, name, forbidden)
        analysis = await intelligence.analyze_view(workspace, task, view)
    forbidden.assert_not_called()
    assert "python" in analysis.manifest.detected_languages
    assert "hidden-language" not in analysis.manifest.detected_languages
    assert "src2/hidden.py" not in analysis.manifest.entry_points
    assert set(analysis.fingerprint.resource_hashes) == {
        entry.relative_path for entry in view.entries}
    assert {node.resource.path for node in analysis.graph.nodes if node.resource} <= set(
        analysis.fingerprint.resource_hashes)
    assert any(edge.relation == "imports" for edge in analysis.graph.edges)
    assert any("captured-project" in item.statement for item in analysis.knowledge.items)
    assert any(item.kind == "interface_endpoint" for item in analysis.knowledge.items)
    assert any(item.kind == "interface_mounted_endpoint" for item in analysis.knowledge.items)
    assert any(item.kind == "database_table" for item in analysis.knowledge.items)
    assert {evidence for item in analysis.knowledge.items for evidence in item.evidence_ids} <= set(
        analysis.fingerprint.resource_hashes)
    assert "restricted captured selection" in analysis.read_plan.purpose
    intelligence._persist_graph.assert_not_called()
    intelligence._persist_knowledge.assert_not_called()


async def test_host_changes_do_not_change_captured_stages(tmp_path):
    from Sprout.workspace.intelligence import WorkspaceIntelligence

    files = {"src/main.py": b"def visible():\n    return 1\n"}
    view = content_view(files)
    (tmp_path / "src").mkdir()
    path = tmp_path / "src/main.py"
    path.write_bytes(b"def hidden():\n    return 999\n")
    service = WorkspaceIntelligence()
    analysis = await service.analyze_view(Workspace("w", tmp_path), Task(workspace_id="w"), view)
    assert any(node.qualified_name == "visible" for node in analysis.graph.nodes)
    assert not any("hidden" in node.name for node in analysis.graph.nodes)
    changed = content_view({"src/main.py": b"def alternate():\n    return 2\n"})
    other = await service.analyze_view(Workspace("w", tmp_path), Task(workspace_id="w"), changed)
    assert analysis.fingerprint.resource_hashes != other.fingerprint.resource_hashes


async def test_invalidated_or_cancelled_view_never_publishes(tmp_path):
    from Sprout.workspace.intelligence import WorkspaceIntelligence

    view = content_view()
    service = WorkspaceIntelligence()
    task = asyncio.create_task(service.analyze_view(Workspace("w", tmp_path),
                                                  Task(workspace_id="w"), view))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(PermissionError):
        view.check()
    with pytest.raises(PermissionError):
        await service.analyze_view(Workspace("w", tmp_path), Task(workspace_id="w"), view)


async def test_parse_failure_budget_or_mismatched_workspace_does_not_publish(tmp_path):
    from Sprout.workspace.authorized_content import ContentLimits
    from Sprout.workspace.intelligence import WorkspaceIntelligence

    bad = content_view({"pyproject.toml": b"invalid [ toml"})
    with pytest.raises(ValueError):
        await WorkspaceIntelligence().analyze_view(Workspace("w", tmp_path),
                                                   Task(workspace_id="w"), bad)
    with pytest.raises(PermissionError):
        bad.check()
    large = content_view({"src/a.py": b"#" * 100001},
                         limits=ContentLimits(max_file_bytes=200000))
    with pytest.raises(ValueError, match="view_graph_source_budget"):
        await WorkspaceIntelligence().analyze_view(Workspace("w", tmp_path),
                                                   Task(workspace_id="w"), large)
    with pytest.raises(PermissionError):
        await WorkspaceIntelligence().analyze_view(Workspace("other", tmp_path),
                                                   Task(workspace_id="other"), content_view())


async def test_python_declared_encoding_and_sql_bom_are_captured(tmp_path):
    from Sprout.workspace.intelligence import WorkspaceIntelligence

    view = content_view({
        "src/latin.py": b"# coding: latin-1\nlabel = '\xe9'\ndef latin():\n    return label\n",
        "src/schema.sql": b"\xef\xbb\xbfCREATE TABLE captured_table (id INTEGER);",
    })
    result = await WorkspaceIntelligence().analyze_view(Workspace("w", tmp_path),
                                                       Task(workspace_id="w"), view)
    assert any(node.qualified_name == "latin" for node in result.graph.nodes)
    assert any("captured_table" in item.statement for item in result.knowledge.items)


def test_view_expiration_is_not_file_authorization(monkeypatch):
    from types import SimpleNamespace

    from Sprout.workspace import authorized_content as module

    view = content_view()
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: view._created + view.limits.max_seconds + 1))
    with pytest.raises(PermissionError):
        view.check()


async def test_frontend_assets_and_imports_only_resolve_captured_files(tmp_path):
    from Sprout.workspace.intelligence import WorkspaceIntelligence

    view = content_view({
        "src/components/Card.tsx": b"export function Card() { return null; }",
        "src/pages/Home.tsx": b"import { Card } from '../components/Card';\n"
                               b"import { Hidden } from '../private/Hidden';\n"
                               b"export function Home() { return Card(); }",
        "src/config/settings.py": b"DEBUG = False",
    })
    result = await WorkspaceIntelligence().analyze_view(Workspace("w", tmp_path),
                                                       Task(workspace_id="w"), view)
    assert any(edge.relation == "component_uses" for edge in result.graph.edges)
    assert any(node.kind == "config_asset" for node in result.graph.nodes)
    assert not any("Hidden" in node.name for node in result.graph.nodes)
