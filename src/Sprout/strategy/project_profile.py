"""Read-only target-project conventions; no command execution or dependency install."""

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from Sprout.workspace.models import Workspace
from Sprout.workspace.scanner import WorkspaceScanner


@dataclass(frozen=True, slots=True)
class ProjectProfile:
    test_directory: str = "tests"
    test_suffix: str = ""
    test_prefix: str = ""
    test_commands: tuple[str, ...] = ()
    lint_commands: tuple[str, ...] = ()


def _read(root, name, parser):
    try:
        path = root / name
        if path.stat().st_size > 1_000_000:
            return {}
        value = parser(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def inspect_project(root, impact):
    root = Path(root)
    manifest = WorkspaceScanner().scan(Workspace(id="strategy", root=root))
    return _profile(
        manifest, impact, lambda name, parser: _read(root, name, parser),
        lambda name: (root / name).is_file(), lambda name: (root / name).is_dir(),
    )


def inspect_view(analysis, impact, view):
    """Same conventions over the authorized content, with no path I/O."""
    view.check(analysis.workspace.id)
    entries = {entry.relative_path: entry for entry in view.entries}
    directories = {parent.as_posix() for name in entries
                   for parent in PurePosixPath(name).parents}

    def read(name, parser):
        view.check()
        entry = entries.get(name)
        if entry is None or len(entry.data) > 1_000_000:
            return {}
        try:
            value = parser(entry.text())
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    return _profile(analysis.manifest, impact, read, entries.__contains__,
                    directories.__contains__)


def _profile(manifest, impact, read, is_file, is_dir):
    languages = set(manifest.detected_languages)
    tests = sorted(item.path for item in impact.items if item.category == "test")
    directory = PurePosixPath(tests[0]).parent.as_posix() if tests else "tests"
    if "python" in languages:
        python_tests = [path for path in tests if path.endswith(".py")]
        if python_tests:
            directory = PurePosixPath(python_tests[0]).parent.as_posix()
        config = read("pyproject.toml", tomllib.loads)
        tools = config.get("tool", {})
        tools = tools if isinstance(tools, dict) else {}
        pytest_config = tools.get("pytest", {})
        pytest_config = pytest_config if isinstance(pytest_config, dict) else {}
        options = pytest_config.get("ini_options", {})
        options = options if isinstance(options, dict) else {}
        configured_paths = options.get("testpaths", [])
        if not python_tests and isinstance(configured_paths, list):
            for candidate in configured_paths:
                if (isinstance(candidate, str) and candidate
                        and not Path(candidate).is_absolute() and ".." not in Path(candidate).parts
                        and is_dir(candidate)):
                    directory = PurePosixPath(candidate).as_posix()
                    break
        ruff = ("ruff" in tools
                or is_file("ruff.toml") or is_file(".ruff.toml"))
        return ProjectProfile(directory, ".py", "test_", ("pytest -q",),
                              ("ruff check .",) if ruff else ())
    if languages & {"node", "typescript"}:
        package = read("package.json", json.loads)
        scripts = package.get("scripts", {})
        scripts = scripts if isinstance(scripts, dict) else {}
        existing = [path for path in tests if path.endswith((".js", ".ts", ".jsx", ".tsx"))]
        if existing:
            directory = PurePosixPath(existing[0]).parent.as_posix()
        suffix = ".test.ts" if "typescript" in languages else ".test.js"
        return ProjectProfile(directory, suffix, "",
            ("npm test",) if isinstance(scripts.get("test"), str)
            and scripts["test"].strip() else (),
            ("npm run lint",) if isinstance(scripts.get("lint"), str)
            and scripts["lint"].strip() else ())
    if is_file("go.mod"):
        return ProjectProfile(".", "_test.go", "", ("go test ./...",))
    if is_file("Cargo.toml"):
        return ProjectProfile("tests", ".rs", "", ("cargo test",))
    return ProjectProfile()
