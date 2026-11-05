"""Tests for Workspace Intelligence analysis."""

from __future__ import annotations

import asyncio
from pathlib import Path

from Sprout.context.builder import ContextBuilder
from Sprout.message.models import Message
from Sprout.session.models import Session
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.memory.knowledge import MemoryKnowledgeStore
from Sprout.storage.local.memory.vectors import MemoryVectorStore
from Sprout.task.models import Task
from Sprout.tests.readonly_host_fixtures import fixture_analysis
from Sprout.tools.project_analyze_tool import (
    ProjectAnalyzeTool,
    _estimate_lines_from_rg_output,
)
from Sprout.tools.workspace_query_tool import WorkspaceQueryTool
from Sprout.workspace.cache import WorkspaceAnalysisCache
from Sprout.workspace.intelligence import WorkspaceIntelligence
from Sprout.workspace.models import ResourceKind, Workspace, WorkspaceKind
from Sprout.workspace.scanner import WorkspaceScanner


async def _analyze_selected(intelligence, workspace, task, *, previous=None):
    """Explicit fixture inventory; not the retired disk-discovery API."""
    return await fixture_analysis(intelligence, workspace, task, paths=(
        "pyproject.toml", "src/package/__init__.py", "src/package/a.py", "src/package/b.py",
        "package.json", "go.mod", "Cargo.toml", "web/frontend/src/components/Button.jsx",
        "web/frontend/src/pages/Home.jsx", "src/routes/users.py", "src/models/user.py",
        "README.md", "Dockerfile", "docker-compose.yml", "src/index.ts", "pom.xml",
        "composer.json", "pubspec.yaml", "CMakeLists.txt",
        "main.tf", "migrations/001_init.sql", "openapi.yaml",
        "src/Sprout/storage/topology.py", "migrations/001.sql",
        "src/main.go", "src/lib.rs"), previous=previous)


class _FakeGraphStore:
    def __init__(self) -> None:
        self.nodes = []
        self.relations = []

    async def merge_node(self, label, key_prop, props):
        self.nodes.append((label, key_prop, dict(props)))

    async def merge_relation(
        self,
        src_label,
        src_key,
        rel,
        dst_label,
        dst_key,
        *,
        src_prop="id",
        dst_prop="id",
        props=None,
    ):
        self.relations.append((rel, src_key, dst_key, (props or {}).get("edge_id")))

    async def drop_node(self, label, key_prop, key_value):
        return 0


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    package = root / "src" / "package"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname='demo-project'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "a.py").write_text(
        "from package.b import Base, value\n"
        "class LocalBase:\n"
        "    pass\n"
        "class Child(LocalBase):\n"
        "    pass\n"
        "def local_value():\n"
        "    return 1\n"
        "def run():\n"
        "    return local_value()\n"
        "def test_value():\n"
        "    assert run() == 1\n"
        "VALUE = local_value()\n",
        encoding="utf-8",
    )
    (package / "b.py").write_text(
        "class Base:\n"
        "    pass\n"
        "def value():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    return root


def test_workspace_intelligence_builds_graph_and_knowledge(tmp_path: Path) -> None:
    root = _project(tmp_path)
    knowledge = MemoryKnowledgeStore()
    vectors = MemoryVectorStore()
    intelligence = WorkspaceIntelligence(
        knowledge_store=knowledge,
        vector_store=vectors,
    )
    workspace = Workspace(
        id="workspace-1",
        root=root,
        kind=WorkspaceKind.LOCAL_DIRECTORY,
    )
    task = Task(
        id="task-1",
        workspace_id=workspace.id,
        instruction="understand project",
        source="test",
    )

    async def run() -> None:
        analysis = await _analyze_selected(intelligence, workspace, task)

        assert analysis.manifest.detected_languages == ("python",)
        assert analysis.read_plan.stage.value == "knowledge"
        assert analysis.read_plan.resources
        assert analysis.graph.nodes
        assert any(node.kind == "class" for node in analysis.graph.nodes)
        assert any(node.kind == "function" for node in analysis.graph.nodes)
        assert all(node.resource is None or node.resource.path in
                   analysis.fingerprint.resource_hashes for node in analysis.graph.nodes)
        assert any(node.granularity == "symbol" for node in analysis.graph.nodes)
        assert any(node.granularity == "module" for node in analysis.graph.nodes)
        assert any(item.kind == "fact" for item in analysis.knowledge.items)
        assert all(set(item.evidence_ids) <= set(analysis.fingerprint.resource_hashes)
                   for item in analysis.knowledge.items)
        assert any(edge.relation == "defines" for edge in analysis.graph.edges)
        assert any(edge.relation == "imports" for edge in analysis.graph.edges)
        assert any(edge.relation == "inherits" for edge in analysis.graph.edges)
        assert any(edge.relation == "calls" for edge in analysis.graph.edges)
        assert any(edge.relation == "tests" for edge in analysis.graph.edges)
        assert any(edge.relation == "references" for edge in analysis.graph.edges)
        assert any(edge.relation == "contains" for edge in analysis.graph.edges)
        assert any(edge.evidence_refs for edge in analysis.graph.edges)
        edge_ids = [edge.id for edge in analysis.graph.edges]
        assert len(edge_ids) == len(set(edge_ids))
        assert "Workspace: workspace-1" in analysis.context_summary()
        assert analysis.query().symbols(kind="class")
        assert analysis.query().symbols(kind="test")
        assert analysis.query().subgraph("Child", max_depth=2).nodes
        assert analysis.query().knowledge("demo-project", kind="fact")
        # Pure analysis never persists; exercise the explicit store component.
        await intelligence._persist_knowledge(analysis.knowledge)
        assert await knowledge.count() > 0
        results = await knowledge.search("demo-project")
        assert any("Project name: demo-project" in item.content for item in results)
        results = await knowledge.search("demo-project")
        assert any("demo-project" in item.content for item in results)
        vector_results = await intelligence.search_knowledge("demo-project")
        assert vector_results

        unchanged = await _analyze_selected(intelligence, workspace, task, previous=analysis)
        assert unchanged.graph.nodes == analysis.graph.nodes
        # Fresh pure analyses issue fresh EvidenceRef IDs; compare every edge's
        # structural identity and source locations rather than cache identity.
        def edges(graph):
            return tuple((e.id, e.source, e.target, e.relation, e.evidence,
                          tuple(ref.display_id() for ref in e.evidence_refs), e.confidence)
                         for e in graph.edges)
        assert edges(unchanged.graph) == edges(analysis.graph)
        assert unchanged.fingerprint.workspace_id == analysis.fingerprint.workspace_id
        assert unchanged.fingerprint.revision == analysis.fingerprint.revision
        assert unchanged.fingerprint.resource_hashes == analysis.fingerprint.resource_hashes
        assert unchanged.changed_resources == ()

        changed_path = root / "src" / "package" / "b.py"
        changed_path.write_text(
            "class Base:\n"
            "    pass\n"
            "def value():\n"
            "    return 2\n",
            encoding="utf-8",
        )
        refreshed = await _analyze_selected(intelligence, workspace, task, previous=analysis)
        assert changed_path.relative_to(root).as_posix() in refreshed.changed_resources
        node_ids = [node.id for node in refreshed.graph.nodes]
        assert len(node_ids) == len(set(node_ids))

        pyproject = root / "pyproject.toml"
        pyproject.write_text(
            "[project]\nname='demo-project-renamed'\nversion='0.1.0'\n",
            encoding="utf-8",
        )
        stale = await _analyze_selected(intelligence, workspace, task, previous=refreshed)
        assert any(
            item_id.startswith("workspace-1:knowledge:")
            for item_id in stale.stale_knowledge_ids
        )
        assert stale.stale_knowledge_items
        assert "Stale knowledge:" in stale.context_summary()

    asyncio.run(run())


def test_workspace_query_tool_returns_symbols(tmp_path: Path) -> None:
    root = _project(tmp_path)
    intelligence = WorkspaceIntelligence(
        knowledge_store=MemoryKnowledgeStore(),
        vector_store=MemoryVectorStore(),
    )
    workspace = Workspace(
        id="workspace-1",
        root=root,
        kind=WorkspaceKind.LOCAL_DIRECTORY,
    )
    task = Task(
        id="task-1",
        workspace_id=workspace.id,
        instruction="understand project",
        source="test",
    )

    async def run() -> None:
        from unittest.mock import AsyncMock

        analysis = await _analyze_selected(intelligence, workspace, task)
        callback = AsyncMock(side_effect=AssertionError("Unbound callback invoked"))
        async def analyze(workspace_id: str):
            return await _analyze_selected(intelligence, workspace, task)

        tool = WorkspaceQueryTool(callback)
        result = await tool.invoke(
            {
                "workspace_id": workspace.id,
                "operation": "symbols",
                "kind": "class",
            }
        )
        assert not result.ok and result.error == "workspace_query_scope_unavailable"
        callback.assert_not_called()
        assert analysis.query().symbols(kind="class")

    asyncio.run(run())


def test_collect_resources_includes_source_files(tmp_path: Path) -> None:
    root = tmp_path / "project"
    package = root / "src" / "package"
    tests = root / "tests"
    package.mkdir(parents=True)
    tests.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname='x'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    for index in range(20):
        (tests / f"test_{index}.py").write_text(
            f"def test_{index}():\n    pass\n",
            encoding="utf-8",
        )
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "core.py").write_text("VALUE = 1\n", encoding="utf-8")

    scanner = WorkspaceScanner()
    workspace = Workspace(id="ws", root=root, kind=WorkspaceKind.LOCAL_DIRECTORY)
    resources = scanner.collect_resources(workspace)
    source_paths = {
        ref.path.replace("\\", "/")
        for ref in resources
        if ref.kind is ResourceKind.SOURCE
    }
    assert any(path.endswith("/package/core.py") for path in source_paths)

    task = Task(
        id="task",
        workspace_id="ws",
        instruction="understand project",
        source="test",
    )
    plan = scanner.build_read_plan(workspace, task)
    assert ResourceKind.SOURCE in {ref.kind for ref in plan.resources}


def test_workspace_analysis_cache_round_trip(tmp_path) -> None:
    from Sprout.workspace.invalidation import snapshot_id

    cache = WorkspaceAnalysisCache()

    async def run() -> None:
        analysis = await _analyze_selected(WorkspaceIntelligence(),
            Workspace(id="workspace-1", root=_project(tmp_path)),
            Task(workspace_id="workspace-1", instruction="analyze"),
        )
        cache.put("workspace-1", analysis)
        version = snapshot_id(analysis)
        assert await cache.get("workspace-1", expected_snapshot_id=version) is analysis
        cache.invalidate("workspace-1")
        assert await cache.get("workspace-1", expected_snapshot_id=version) is None

    asyncio.run(run())


def test_context_builder_injects_structured_workspace(tmp_path: Path) -> None:
    root = _project(tmp_path)
    intelligence = WorkspaceIntelligence(
        knowledge_store=MemoryKnowledgeStore(),
        vector_store=MemoryVectorStore(),
    )
    workspace = Workspace(
        id="workspace-1",
        root=root,
        kind=WorkspaceKind.LOCAL_DIRECTORY,
    )
    task = Task(
        id="task-1",
        workspace_id=workspace.id,
        instruction="understand project",
        source="test",
    )

    async def run() -> None:
        from unittest.mock import AsyncMock

        analysis = await _analyze_selected(intelligence, workspace, task)
        async def analyze(workspace_id: str):
            return await _analyze_selected(intelligence, workspace, task)

        callback = AsyncMock(side_effect=AssertionError("Unbound context provider invoked"))
        storage = StorageBundle.in_memory()
        builder = ContextBuilder(
            storage,
            workspace_analysis_provider=callback,
        )
        context = await builder.build(
            message=Message(
                "hello",
                metadata={"workspace_id": workspace.id},
            ),
            session=Session(id="session-1", user_id="user-1"),
            tools={},
            skills={},
        )
        assert context.workspace is None
        assert "workspace_context" not in context.metadata
        callback.assert_not_called()
        # Synchronous projection is data formatting, not a read/publication grant.
        projected = builder._isolated_context(Message("hello"), context.session, {}, {},
                                             analysis=analysis)
        assert projected.workspace.graph_nodes and projected.workspace.graph_edges

        targeted = await builder.build(
            message=Message(
                "Child",
                metadata={"workspace_id": workspace.id},
            ),
            session=Session(id="session-2", user_id="user-1"),
            tools={},
            skills={},
        )
        assert targeted.workspace is None
        callback.assert_not_called()
        targeted = builder._isolated_context(Message("Child"), targeted.session, {}, {},
                                            analysis=analysis)
        assert any(
            "Child" in node.name
            for node in targeted.workspace.graph_nodes
        )

    asyncio.run(run())


def test_generic_workspace_knowledge(tmp_path: Path) -> None:
    root = tmp_path / "generic"
    root.mkdir()
    (root / "package.json").write_text(
        '{"name":"web-app","scripts":{"build":"vite build"},"dependencies":{"react":"^18"}}',
        encoding="utf-8",
    )
    (root / "go.mod").write_text(
        "module example.com/service\n\ngo 1.22\n",
        encoding="utf-8",
    )
    (root / "Cargo.toml").write_text(
        "[package]\nname='rust-service'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    components_dir = root / "web" / "frontend" / "src" / "components"
    components_dir.mkdir(parents=True)
    (components_dir / "Button.jsx").write_text(
        "export default function Button() { return null; }\n",
        encoding="utf-8",
    )
    pages_dir = root / "web" / "frontend" / "src" / "pages"
    pages_dir.mkdir(parents=True)
    (pages_dir / "Home.jsx").write_text(
        "import Button from '../components/Button';\n"
        "export default function Home() { return <Button />; }\n",
        encoding="utf-8",
    )
    routes_dir = root / "src" / "routes"
    routes_dir.mkdir(parents=True)
    (routes_dir / "users.py").write_text(
        "def get_user():\n    return {}\n",
        encoding="utf-8",
    )
    models_dir = root / "src" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "user.py").write_text(
        "class User:\n    pass\n",
        encoding="utf-8",
    )
    intelligence = WorkspaceIntelligence(
        knowledge_store=MemoryKnowledgeStore(),
        vector_store=MemoryVectorStore(),
    )
    workspace = Workspace(
        id="generic-1",
        root=root,
        kind=WorkspaceKind.LOCAL_DIRECTORY,
    )
    task = Task(
        id="task-generic",
        workspace_id=workspace.id,
        instruction="understand generic project",
        source="test",
    )

    async def run() -> None:
        analysis = await _analyze_selected(intelligence, workspace, task)
        statements = [item.statement for item in analysis.knowledge.items]
        assert any("Node package: web-app" in item for item in statements)
        assert any("Go module: example.com/service" in item for item in statements)
        assert any("Rust crate: rust-service" in item for item in statements)
        assert any(node.kind == "component" for node in analysis.graph.nodes)
        assert any(node.kind == "page" for node in analysis.graph.nodes)
        assert any(edge.relation == "component_uses" for edge in analysis.graph.edges)
        assert any(node.kind == "api_route" for node in analysis.graph.nodes)
        assert any(node.kind == "data_asset" for node in analysis.graph.nodes)

    asyncio.run(run())


def test_workspace_analysis_covers_project_surface_and_database_layers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "surface"
    root.mkdir()
    (root / "README.md").write_text("# Surface\n", encoding="utf-8")
    (root / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / ".env.example").write_text(
        "DATABASE_URL=postgres://example\nSECRET_TOKEN=do-not-copy\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        '{"name":"surface","main":"src/index.ts","scripts":{"build":"tsc"}}',
        encoding="utf-8",
    )
    src = root / "src"
    src.mkdir()
    (src / "index.ts").write_text("export function main() {}\n", encoding="utf-8")
    (root / "go.mod").write_text("module example.com/surface\n", encoding="utf-8")
    (root / "Cargo.toml").write_text(
        "[package]\nname='surface'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (root / "pom.xml").write_text("<project />\n", encoding="utf-8")
    (root / "composer.json").write_text('{"name":"acme/surface"}', encoding="utf-8")
    (root / "Package.swift").write_text("// swift-tools-version: 5.9\n", encoding="utf-8")
    (root / "pubspec.yaml").write_text("name: surface\n", encoding="utf-8")
    (root / "mix.exs").write_text("defmodule Surface.MixProject do\nend\n", encoding="utf-8")
    (root / "CMakeLists.txt").write_text("project(surface)\n", encoding="utf-8")
    (root / "main.tf").write_text("terraform {}\n", encoding="utf-8")
    prisma = root / "prisma"
    prisma.mkdir()
    (prisma / "schema.prisma").write_text("model User { id Int @id }\n", encoding="utf-8")
    migrations = root / "migrations"
    migrations.mkdir()
    (migrations / "001_init.sql").write_text("create table users(id int);\n", encoding="utf-8")
    (root / "openapi.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")
    proto = root / "proto"
    proto.mkdir()
    (proto / "user.proto").write_text('syntax = "proto3";\n', encoding="utf-8")

    intelligence = WorkspaceIntelligence(
        knowledge_store=MemoryKnowledgeStore(),
        vector_store=MemoryVectorStore(),
    )
    workspace = Workspace(
        id="surface-1",
        root=root,
        kind=WorkspaceKind.LOCAL_DIRECTORY,
    )
    task = Task(
        id="task-surface",
        workspace_id=workspace.id,
        instruction="understand full project surface",
        source="test",
    )

    async def run() -> None:
        analysis = await _analyze_selected(intelligence, workspace, task)
        languages = set(analysis.manifest.detected_languages)
        assert {
            "node",
            "typescript",
            "go",
            "rust",
            "java",
            "php",
            "dart",
            "c-cpp",
            "terraform",
            "sql",
        } <= languages
        assert "docker" in analysis.manifest.framework_hints
        assert "docker-compose" in analysis.manifest.framework_hints
        assert "project-database" not in analysis.manifest.framework_hints
        statements = [item.statement for item in analysis.knowledge.items]
        assert any("Captured documentation assets: README.md" in item for item in statements)
        assert any("Dockerfile" in item for item in statements)
        assert any("docker-compose.yml" in item for item in statements)
        assert ".env.example" not in analysis.fingerprint.resource_hashes
        assert not any("DATABASE_URL" in item for item in statements)
        assert not any("postgres://example" in item for item in statements)
        assert any("Captured project_database assets:" in item for item in statements)
        assert any("Captured interface assets:" in item for item in statements)
        assert "main.tf" in analysis.fingerprint.resource_hashes
        assert not any("Sprout database topology:" in item for item in statements)

    asyncio.run(run())


def test_workspace_analysis_marks_sprout_database_separately(tmp_path: Path) -> None:
    root = tmp_path / "sproutish"
    topology = root / "src" / "Sprout" / "storage"
    topology.mkdir(parents=True)
    (topology / "topology.py").write_text("ENTITY_OWNERSHIP = ()\n", encoding="utf-8")
    (root / "migrations").mkdir()
    (root / "migrations" / "001.sql").write_text("create table demo(id int);\n", encoding="utf-8")

    intelligence = WorkspaceIntelligence(
        knowledge_store=MemoryKnowledgeStore(),
        vector_store=MemoryVectorStore(),
    )
    workspace = Workspace(id="sproutish-1", root=root)
    task = Task(
        id="task-sproutish",
        workspace_id=workspace.id,
        instruction="analyze database layers",
        source="test",
    )

    async def run() -> None:
        analysis = await _analyze_selected(intelligence, workspace, task)
        by_kind = {item.kind: item.statement for item in analysis.knowledge.items}
        # A filename alone cannot certify a runtime's database topology.
        assert "sprout_database" not in by_kind
        assert "src/Sprout/storage/topology.py" in analysis.fingerprint.resource_hashes
        assert "project_database" in by_kind
        assert "Captured project_database assets:" in by_kind["project_database"]

    asyncio.run(run())


def test_project_analyze_tool_returns_project_surface(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "tool-project"
    root.mkdir()
    (root / "README.md").write_text("# Tool Project\n", encoding="utf-8")
    (root / "package.json").write_text('{"name":"tool-project"}', encoding="utf-8")
    (root / "openapi.json").write_text('{"openapi":"3.1.0"}', encoding="utf-8")

    async def run() -> None:
        from Sprout.tests.conftest import build_runtime
        from Sprout.tests.readonly_host_fixtures import native_analysis_subject

        storage = StorageBundle.in_memory()
        from Sprout.storage.local.sqlite.metadata import open_metadata_store

        storage.metadata = open_metadata_store(str(tmp_path / "metadata.db"))
        runtime, _ = build_runtime(storage=storage)
        results = []
        invoke = ProjectAnalyzeTool.invoke

        async def observe(tool, arguments):
            result = await invoke(tool, arguments)
            results.append(result)
            return result

        monkeypatch.setattr(ProjectAnalyzeTool, "invoke", observe)
        try:
            workspace = await runtime.open_workspace(root)
            subject = await native_analysis_subject(
                runtime, workspace, ("README.md", "package.json", "openapi.json"))
            await runtime.analyze_workspace(workspace.id, **subject)
            assert len(results) == 1
            result = results[0]
            assert result.ok is True
            assert result.data["languages"] == ["node"]
            # Windows resource keys are case-folded by the existing path
            # contract. Match that identity without weakening evidence scope.
            statements = [item["statement"].casefold() for item in result.data["knowledge"]]
            # Captured-view facts explicitly identify their bounded evidence;
            # the retired disk analyzer's whole-project wording is not reused.
            assert "captured documentation assets: readme.md" in statements
            assert "captured interface assets: openapi.json" in statements
            evidence = {item["statement"].casefold(): [p.casefold() for p in item["evidence_ids"]]
                        for item in result.data["knowledge"]}
            assert evidence["captured documentation assets: readme.md"] == ["readme.md"]
            assert evidence["captured interface assets: openapi.json"] == ["openapi.json"]
        finally:
            await runtime.stop()
            await storage.close()

    asyncio.run(run())


def test_project_analyze_estimates_lines_from_rg_files(tmp_path: Path) -> None:
    root = tmp_path / "line-project"
    root.mkdir()
    (root / "app.py").write_text("print('hi')\nprint('bye')\n", encoding="utf-8")
    (root / "README.md").write_text("# Title\n\nbody", encoding="utf-8")
    (root / "image.bin").write_bytes(b"\0binary")

    estimate = _estimate_lines_from_rg_output(
        root,
        {"ok": True, "stdout": "app.py\nREADME.md\nimage.bin\n"},
    )

    assert estimate["ok"] is True
    assert estimate["source"] == "rg --files fallback"
    assert estimate["files"] == 2
    assert estimate["lines"] == 5
    assert estimate["skipped_files"] == 1
    assert estimate["by_extension"][".py"] == {"files": 1, "lines": 2}
    assert estimate["by_extension"][".md"] == {"files": 1, "lines": 3}


def test_workspace_intelligence_ingests_skills(tmp_path: Path) -> None:
    root = _project(tmp_path)
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: my-skill\n"
        "description: Test skill\n"
        "required_tools: file.read, search\n"
        "---\n\n# Skill body\n",
        encoding="utf-8",
    )
    intelligence = WorkspaceIntelligence(
        knowledge_store=MemoryKnowledgeStore(),
        vector_store=MemoryVectorStore(),
        skills_dir=str(skills_dir),
    )
    workspace = Workspace(
        id="workspace-1",
        root=root,
        kind=WorkspaceKind.LOCAL_DIRECTORY,
    )
    task = Task(
        id="task-1",
        workspace_id=workspace.id,
        instruction="understand project",
        source="test",
    )

    async def run() -> None:
        analysis = await _analyze_selected(intelligence, workspace, task)
        from Sprout.workspace.skills import SkillWorkspaceAnalyzer

        assert not any(node.kind == "skill" for node in analysis.graph.nodes)
        # Skill discovery is its own explicit host component, not a side effect
        # of a restricted project content view.
        skills = SkillWorkspaceAnalyzer().analyze(workspace.id, skills_dir)
        assert any(node.kind == "skill" for node in skills.nodes)
        assert any(node.kind == "tool" for node in skills.nodes)
        assert any(edge.relation == "requires" for edge in skills.edges)
        assert any(item.kind == "skill" for item in skills.knowledge_items)

    asyncio.run(run())


def test_workspace_intelligence_persists_graph(tmp_path: Path) -> None:
    root = _project(tmp_path)
    graph_store = _FakeGraphStore()
    intelligence = WorkspaceIntelligence(
        knowledge_store=MemoryKnowledgeStore(),
        vector_store=MemoryVectorStore(),
        graph_store=graph_store,
    )
    workspace = Workspace(
        id="workspace-1",
        root=root,
        kind=WorkspaceKind.LOCAL_DIRECTORY,
    )
    task = Task(
        id="task-1",
        workspace_id=workspace.id,
        instruction="understand project",
        source="test",
    )

    async def run() -> None:
        analysis = await _analyze_selected(intelligence, workspace, task)
        # Persistence is tested explicitly, not as an unscoped analyzer effect.
        await intelligence._persist_graph(analysis.graph)
        assert graph_store.nodes
        assert graph_store.relations

    asyncio.run(run())


def test_generic_source_symbols_in_graph(tmp_path: Path) -> None:
    root = tmp_path / "polyglot"
    src = root / "src"
    src.mkdir(parents=True)
    (src / "main.go").write_text(
        "package main\nfunc run() {}\ntype Server struct {}\n",
        encoding="utf-8",
    )
    (src / "lib.rs").write_text(
        "pub fn compute() {}\npub struct Config {}\n",
        encoding="utf-8",
    )
    (src / "index.ts").write_text(
        "export function start() {}\nexport class App {}\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text('{"name":"polyglot"}', encoding="utf-8")

    intelligence = WorkspaceIntelligence(
        knowledge_store=MemoryKnowledgeStore(),
        vector_store=MemoryVectorStore(),
    )
    workspace = Workspace(
        id="poly-1",
        root=root,
        kind=WorkspaceKind.LOCAL_DIRECTORY,
    )
    task = Task(
        id="task-poly",
        workspace_id=workspace.id,
        instruction="understand project",
        source="test",
    )

    async def run() -> None:
        analysis = await _analyze_selected(intelligence, workspace, task)
        names = {node.qualified_name for node in analysis.graph.nodes}
        kinds = {node.kind for node in analysis.graph.nodes}
        assert "class" in kinds
        assert "function" in kinds
        assert {"Server", "compute", "start"} <= names
        assert any(edge.relation == "defines" for edge in analysis.graph.edges)

    asyncio.run(run())


def test_analysis_serialization_round_trip(tmp_path: Path) -> None:
    from Sprout.workspace.intelligence import WorkspaceAnalysis
    from Sprout.workspace.serialization import from_jsonable, to_jsonable

    root = _project(tmp_path)
    intelligence = WorkspaceIntelligence(
        knowledge_store=MemoryKnowledgeStore(),
        vector_store=MemoryVectorStore(),
    )
    workspace = Workspace(
        id="workspace-1",
        root=root,
        kind=WorkspaceKind.LOCAL_DIRECTORY,
    )
    task = Task(
        id="task-1",
        workspace_id=workspace.id,
        instruction="understand project",
        source="test",
    )

    async def run() -> None:
        analysis = await _analyze_selected(intelligence, workspace, task)
        restored = from_jsonable(to_jsonable(analysis), WorkspaceAnalysis)
        assert restored.workspace.id == analysis.workspace.id
        assert restored.workspace.root == analysis.workspace.root
        assert restored.fingerprint.resource_hashes == analysis.fingerprint.resource_hashes
        assert len(restored.graph.nodes) == len(analysis.graph.nodes)
        assert len(restored.graph.edges) == len(analysis.graph.edges)
        assert {node.id for node in restored.graph.nodes} == {
            node.id for node in analysis.graph.nodes
        }
        assert {edge.id for edge in restored.graph.edges} == {
            edge.id for edge in analysis.graph.edges
        }
        assert [item.statement for item in restored.knowledge.items] == [
            item.statement for item in analysis.knowledge.items
        ]
        assert restored.read_plan.stage == analysis.read_plan.stage
        assert restored.stale_knowledge_ids == analysis.stale_knowledge_ids

    asyncio.run(run())


def test_analysis_cache_hot_lane_round_trip(tmp_path: Path) -> None:
    from Sprout.storage.local.memory import MemoryCacheStore
    from Sprout.workspace.invalidation import snapshot_id

    root = _project(tmp_path)
    intelligence = WorkspaceIntelligence(
        knowledge_store=MemoryKnowledgeStore(),
        vector_store=MemoryVectorStore(),
    )
    workspace = Workspace(
        id="workspace-1",
        root=root,
        kind=WorkspaceKind.LOCAL_DIRECTORY,
    )
    task = Task(
        id="task-1",
        workspace_id=workspace.id,
        instruction="understand project",
        source="test",
    )

    async def run() -> None:
        analysis = await _analyze_selected(intelligence, workspace, task)
        cache = WorkspaceAnalysisCache(cache_store=MemoryCacheStore())
        await cache.persist("workspace-1", analysis)
        restored = await cache.get("workspace-1", expected_snapshot_id=snapshot_id(analysis))
        assert restored is not None
        assert {node.id for node in restored.graph.nodes} == {
            node.id for node in analysis.graph.nodes
        }
        assert [item.statement for item in restored.knowledge.items] == [
            item.statement for item in analysis.knowledge.items
        ]
        await cache.forget("workspace-1")
        assert await cache.get("workspace-1", expected_snapshot_id=snapshot_id(analysis)) is None

    asyncio.run(run())
