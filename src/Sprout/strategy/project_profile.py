"""Read-only target-project conventions; no command execution or dependency install."""

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

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
    languages = set(manifest.detected_languages)
    tests = sorted(item.path for item in impact.items if item.category == "test")
    directory = Path(tests[0]).parent.as_posix() if tests else "tests"
    if "python" in languages:
        python_tests = [path for path in tests if path.endswith(".py")]
        if python_tests:
            directory = Path(python_tests[0]).parent.as_posix()
        config = _read(root, "pyproject.toml", tomllib.loads)
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
                        and (root / candidate).is_dir()):
                    directory = Path(candidate).as_posix()
                    break
        ruff = ("ruff" in tools
                or (root / "ruff.toml").is_file() or (root / ".ruff.toml").is_file())
        return ProjectProfile(directory, ".py", "test_", ("pytest -q",),
                              ("ruff check .",) if ruff else ())
    if languages & {"node", "typescript"}:
        package = _read(root, "package.json", json.loads)
        scripts = package.get("scripts", {})
        scripts = scripts if isinstance(scripts, dict) else {}
        existing = [path for path in tests if path.endswith((".js", ".ts", ".jsx", ".tsx"))]
        if existing:
            directory = Path(existing[0]).parent.as_posix()
        suffix = ".test.ts" if "typescript" in languages else ".test.js"
        return ProjectProfile(directory, suffix, "",
            ("npm test",) if isinstance(scripts.get("test"), str)
            and scripts["test"].strip() else (),
            ("npm run lint",) if isinstance(scripts.get("lint"), str)
            and scripts["lint"].strip() else ())
    if (root / "go.mod").is_file():
        return ProjectProfile(".", "_test.go", "", ("go test ./...",))
    if (root / "Cargo.toml").is_file():
        return ProjectProfile("tests", ".rs", "", ("cargo test",))
    return ProjectProfile()
