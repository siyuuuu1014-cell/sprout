"""Regression checks for project facts and their actual supporting files."""

from pathlib import Path

from Sprout.workspace.knowledge import ProjectKnowledgeBuilder
from Sprout.workspace.models import Workspace
from Sprout.workspace.scanner import WorkspaceScanner


def _write(root: Path, name: str, content: str = "") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _analyze(root: Path):
    workspace = Workspace(id="precision", root=root)
    manifest = WorkspaceScanner().scan(workspace)
    return ProjectKnowledgeBuilder().build(workspace, manifest)


def test_manifest_facts_have_only_their_own_evidence(tmp_path):
    python = _write(tmp_path, "pyproject.toml", '[project]\nname = "python-app"\n')
    node = _write(tmp_path, "package.json", '{"name":"node-app","scripts":{"test":"vitest"}}')
    go = _write(tmp_path, "go.mod", "module example.org/app\n")
    rust = _write(tmp_path, "Cargo.toml", '[package]\nname = "rust-app"\n')
    facts = {item.statement: item for item in _analyze(tmp_path).items}
    for statement, path in (
        ("Project name: python-app", python),
        ("Node package: node-app", node),
        ("Node scripts: test", node),
        ("Go module: example.org/app", go),
        ("Rust crate: rust-app", rust),
    ):
        assert facts[statement].evidence_ids == (path.resolve().as_posix(),)
    commands = next(item for item in facts.values() if item.statement.startswith("Test commands:"))
    assert commands.kind == "inference"
    assert commands.confidence < 1


def test_api_parent_does_not_classify_every_file_as_an_interface(tmp_path):
    root = tmp_path / "api" / "project"
    _write(root, "src/main.py")
    route = _write(root, "src/routes/login.py")
    contracts = [item for item in _analyze(root).items if item.kind == "interface"]
    assert len(contracts) == 1
    assert contracts[0].evidence_ids == (route.resolve().as_posix(),)


def test_scratch_files_do_not_create_languages_frameworks_or_test_facts(tmp_path):
    _write(tmp_path, "src/app.py")
    _write(tmp_path, ".workbuddy/test-runs/test_fixture.py")
    _write(tmp_path, ".workbuddy/test-runs/package.json", '{"name":"fixture"}')
    _write(tmp_path, ".workbuddy/test-runs/contract.proto")
    _write(tmp_path, "NODE_MODULES/fixture/test_vendor.py")
    workspace = Workspace(id="precision", root=tmp_path)
    scanner = WorkspaceScanner()
    manifest = scanner.scan(workspace)
    assert manifest.detected_languages == ("python",)
    assert "protobuf" not in manifest.framework_hints
    assert len(scanner.collect_resources(workspace)) == 1
    knowledge = ProjectKnowledgeBuilder().build(workspace, manifest)
    assert not any("pytest-style" in item.statement for item in knowledge.items)
    assert not any("fixture" in item.statement for item in knowledge.items)


def test_nested_tests_cite_existing_files_not_a_nonexistent_root_directory(tmp_path):
    test = _write(tmp_path, "src/app/tests/test_app.py")
    item = next(item for item in _analyze(tmp_path).items if "pytest-style" in item.statement)
    assert item.evidence_ids == (test.resolve().as_posix(),)
    assert all(Path(evidence).is_file() for evidence in item.evidence_ids)


def test_surface_inventory_does_not_silently_drop_files_after_thirty(tmp_path):
    paths = [_write(tmp_path, f"contracts/{index:02}.proto") for index in range(35)]
    item = next(item for item in _analyze(tmp_path).items if item.kind == "interface")
    assert set(item.evidence_ids) == {path.resolve().as_posix() for path in paths}


def test_malformed_manifest_encoding_and_json_shape_do_not_abort_analysis(tmp_path):
    _write(tmp_path, "package.json", "[]")
    _write(tmp_path, "pyproject.toml").write_bytes(b"\xff\xfe")
    _write(tmp_path, ".env").write_bytes(b"\xff")
    knowledge = _analyze(tmp_path)
    assert not any(item.statement.startswith("Node package:") for item in knowledge.items)
    assert not any(item.statement.startswith("Project name:") for item in knowledge.items)
