import json
import re
from pathlib import PurePosixPath

import pytest

from Sprout.strategy.pipeline import StrategyPipeline


def write(root, path, text=""):
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("files,directory", [
    ({"app.py": "value=1"}, "tests"),
    ({"app.py": "", "test_existing.py": ""}, "."),
    ({"src/demo/__init__.py": "", "src/demo/tests/test_existing.py": ""}, "src/demo/tests"),
    ({"app.py": "", "checks/.keep": "", "pyproject.toml":
      '[tool.pytest.ini_options]\ntestpaths=["checks"]'}, "checks"),
])
def test_python_layout_and_safe_test_filename(tmp_path, files, directory):
    for path, content in files.items():
        write(tmp_path, path, content)
    plan = StrategyPipeline().plan_user_request(tmp_path, "Add increment(value)+1; 正负/零")
    path = PurePosixPath(plan.test_files[0])
    assert str(path.parent) == directory
    assert re.fullmatch(r"test_[a-z0-9_]+\.py", path.name)
    assert "Sprout" not in str(path)
    assert [item.command for item in plan.verification.criteria] == ["pytest -q"]


def test_ruff_requires_configuration_and_checks_target_root(tmp_path):
    write(tmp_path, "app.py", "value=1")
    write(tmp_path, "pyproject.toml", "[tool.ruff]\nline-length=100")
    plan = StrategyPipeline().plan_user_request(tmp_path, "Add increment")
    assert [item.command for item in plan.verification.criteria] == ["pytest -q", "ruff check ."]


def test_node_uses_declared_scripts_not_python_defaults(tmp_path):
    write(tmp_path, "src/app.ts", "export const value=1")
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": "vitest run",
                                                           "lint": "eslint ."}}))
    plan = StrategyPipeline().plan_user_request(tmp_path, "Add increment(value)")
    assert plan.test_files[0].endswith(".test.ts")
    assert [item.command for item in plan.verification.criteria] == ["npm test", "npm run lint"]


def test_node_without_test_script_does_not_invent_one(tmp_path):
    write(tmp_path, "package.json", '{"scripts":{"build":"tsc"}}')
    plan = StrategyPipeline().plan_user_request(tmp_path, "Add increment")
    assert plan.verification.criteria == ()
    assert any("验收配置" in risk for risk in plan.risks)


@pytest.mark.parametrize("manifest,command,suffix", [
    ("go.mod", "go test ./...", "_test.go"),
    ("Cargo.toml", "cargo test", ".rs"),
])
def test_native_project_commands(tmp_path, manifest, command, suffix):
    write(tmp_path, manifest)
    plan = StrategyPipeline().plan_user_request(tmp_path, "Add increment")
    assert plan.test_files[0].endswith(suffix)
    assert [item.command for item in plan.verification.criteria] == [command]


def test_unknown_project_requires_configuration(tmp_path):
    plan = StrategyPipeline().plan_user_request(tmp_path, "新增功能")
    assert plan.test_files == ()
    assert plan.verification.criteria == ()
    assert any("验收配置" in risk for risk in plan.risks)


def test_matched_existing_tests_are_preserved(tmp_path):
    write(tmp_path, "app.py")
    write(tmp_path, "quality/test_billing.py")
    plan = StrategyPipeline().plan_user_request(tmp_path, "fix billing")
    assert plan.test_files == ("quality/test_billing.py",)


def test_flat_project_task_spec_does_not_leak_sprout_layout(tmp_path):
    from Sprout.strategy.execution import ExecutionPlanner

    write(tmp_path, "app.py", "value=1")
    plan = StrategyPipeline().plan_user_request(tmp_path, "Add increment(value)+1; test zero")
    spec = ExecutionPlanner().plan_to_task_spec(plan)
    assert "src/Sprout" not in spec.instruction
    assert "ruff check src" not in spec.instruction
    assert spec.metadata["test_files"][0].startswith("tests/test_strategy_")
    assert all(item["command"] == "pytest -q" for item in spec.metadata["verification_commands"])


@pytest.mark.asyncio
async def test_database_only_planning_does_not_scan_filesystem(tmp_path, monkeypatch):
    from Sprout.llm.messages import LLMResponse

    def forbidden(*args):
        pytest.fail("Database-only planning must not scan the filesystem")

    async def search(*args):
        return []

    class Model:
        name = "offline"
        model = "offline"

        async def chat(self, *args, **kwargs):
            return LLMResponse(content='{"mode":"add","strategy":"offline","steps":[]}')

    monkeypatch.setattr("Sprout.strategy.pipeline.inspect_project", forbidden)
    plan = await StrategyPipeline().plan_from_project_database(
        tmp_path, "new feature", search, Model())
    assert plan.verification.criteria == ()
