"""Real entry points create tasks through strategy planning.

The bridge is best-effort: planning adds step decomposition to the execution
graph, but a missing model, a non-Git workspace, or a planner bug must never
cost the user their request. These tests pin both halves of that contract.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from Sprout.config.loader import default_settings
from Sprout.llm.messages import LLMResponse
from Sprout.runtime.factory import create_runtime
from Sprout.task.models import DelegationScope, TaskExecutionMode


def _host_inputs(root):
    from Sprout.gateway.local_identity import current_cli_principal

    principal = current_cli_principal()
    paths = ("src/user.py", "pyproject.toml")
    return dict(actor=principal, source=principal.source,
                delegation_scope=DelegationScope(
                    allowed_paths=tuple(str(root / path) for path in paths),
                    allowed_actions=frozenset({"file.read", "tool.invoke"})),
                preparation_paths=paths)


class _StepModel:
    """Returns a fixed structured plan; stands in for a configured provider."""

    name = "step-model"
    model = "step-model"

    async def chat(self, messages, *, tools=()):
        return LLMResponse(
            content=(
                '{"mode":"modify","strategy":"先查再改",'
                '"steps":[{"description":"读 user 模块","kind":"analysis",'
                '"target_paths":["src/user.py"]},'
                '{"description":"加导出函数","kind":"edit",'
                '"target_paths":["src/user.py"]}],'
                '"test_files":[],"risks":[]}'
            )
        )


class _BrokenModel:
    """Every call raises, like a missing key or a dead endpoint."""

    name = "broken"
    model = "broken"

    async def chat(self, messages, *, tools=()):
        raise RuntimeError("no api key configured")


def _settings(tmp_path: Path):
    settings = default_settings()
    settings.model.provider = "echo"
    settings.model.model = "echo-1"
    db = tmp_path / "conv.db"
    settings.storage.operational = "memory://"
    settings.storage.knowledge = "memory://"
    settings.storage.observations.enabled = False
    settings.storage.metadata = f"sqlite:///{db.as_posix()}"
    settings.storage.session = f"sqlite:///{db.as_posix()}"
    settings.storage.trajectory_dir = str(tmp_path / "traj")
    settings.storage.blobs_dir = str(tmp_path / "blobs")
    settings.security.audit.path = str(tmp_path / "audit.jsonl")
    return settings


def _git_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "user.py").write_text(
        "def list_users():\n    return []\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    return root


def test_planning_produces_steps_for_the_graph(tmp_path: Path) -> None:
    """A planned task carries steps, so the compiler emits SUBTASK nodes."""
    settings = _settings(tmp_path)
    root = _git_workspace(tmp_path)

    async def run() -> None:
        runtime = create_runtime(settings)
        runtime.models.register(_StepModel(), default=True)
        workspace = await runtime.open_workspace(root)

        task = await runtime.create_task_planned(
            workspace.id,
            "给 user 模块加导出",
            use_model_planner=True,
            language="zh",
            **_host_inputs(root),
        )

        assert task.metadata["plan_kind"] == "model"
        assert task.metadata["response_language"] == "zh"
        assert [step["kind"] for step in task.metadata["steps"]] == [
            "analysis",
            "edit",
        ]
        await runtime.stop()

    asyncio.run(run())


def test_model_planner_is_off_by_default(tmp_path: Path) -> None:
    """Decomposition is opt-in: it measured slower for no agent-side gain.

    An A/B on a real requirement put the model-planned path at 78.5s against
    23.0s plain, with the edit round taking the same time either way. Keep
    the default off unless that evidence is overturned.
    """
    settings = _settings(tmp_path)
    root = _git_workspace(tmp_path)

    async def run() -> None:
        runtime = create_runtime(settings)
        runtime.models.register(_StepModel(), default=True)
        workspace = await runtime.open_workspace(root)

        task = await runtime.create_task_planned(workspace.id, "给 user 模块加导出",
                                                 **_host_inputs(root))

        # The local plan runs (cheap, ~1s) but yields no structured steps, so
        # the graph is the pre-decomposition shape.
        assert task.metadata["plan_kind"] == "local"
        assert task.metadata.get("steps") in (None, [])
        assert "导出" in task.instruction
        await runtime.stop()

    asyncio.run(run())


def test_planning_failure_falls_back_to_a_plain_task(tmp_path: Path) -> None:
    """A known pre-start preparation failure can still create a plain task."""
    settings = _settings(tmp_path)
    root = _git_workspace(tmp_path)

    async def run() -> None:
        runtime = create_runtime(settings)
        runtime.models.register(_BrokenModel(), default=True)
        workspace = await runtime.open_workspace(root)
        runtime._capture_preparation = AsyncMock(
            side_effect=RuntimeError("Host preparation failed before starting an owned node"))

        task = await runtime.create_task_planned(
            workspace.id, "给 user 模块加导出", use_model_planner=True, **_host_inputs(root)
        )

        assert task.metadata["plan_kind"] == "fallback"
        assert task.metadata.get("steps") in (None, [])
        # The instruction survives verbatim.
        assert "导出" in task.instruction
        assert await runtime.storage.metadata.list_execution_nodes(task.id) == []
        runtime._capture_preparation.assert_awaited_once()
        await runtime.stop()

    asyncio.run(run())


async def _assert_uncertain_preparation(runtime):
    tasks = await runtime.list_tasks()
    assert tasks and all(task.execution_mode is TaskExecutionMode.READ_ONLY for task in tasks)
    nodes = [node for task in tasks
             for node in await runtime.storage.metadata.list_execution_nodes(task.id)]
    assert nodes and all(node.metadata["execution_guard"]["state"] == "uncertain"
                         for node in nodes)
    for node in nodes:
        assert await runtime.storage.metadata.get_node_result_source(node.id) is None


def test_owned_planner_failure_is_not_unscoped_fallback(tmp_path):
    root = _git_workspace(tmp_path)

    async def run():
        runtime = create_runtime(_settings(tmp_path))
        runtime.models.register(_BrokenModel(), default=True)
        workspace = await runtime.open_workspace(root)
        try:
            with pytest.raises(PermissionError, match="analysis_publication_unavailable"):
                await runtime.create_task_planned(workspace.id, "修改 user 模块",
                    use_model_planner=True, **_host_inputs(root))
            await _assert_uncertain_preparation(runtime)
        finally:
            await runtime.stop()

    asyncio.run(run())


def test_gateway_create_task_goes_through_planning(tmp_path: Path) -> None:
    """The real gateway entry point must use the bridge, not a bare create.

    Asserted on ``plan_kind`` rather than on steps: the gateway deliberately
    inherits the off-by-default model planner, so steps are absent. What
    matters is that it routes through the bridge (which can decompose) and
    not straight to a bare ``create_task``.
    """
    from Sprout.gateway.project_gateway import ProjectGateway

    settings = _settings(tmp_path)
    root = _git_workspace(tmp_path)

    async def run() -> None:
        runtime = create_runtime(settings)
        runtime.models.register(_StepModel(), default=True)
        workspace = await runtime.open_workspace(root)

        gateway = ProjectGateway(runtime, transport="cli", default_user="tester")
        inputs = _host_inputs(root)
        task = await gateway.create_task(workspace.id, "给 user 模块加导出",
            principal=inputs["actor"], delegation_scope=inputs["delegation_scope"],
            preparation_paths=inputs["preparation_paths"])

        # "local" is produced only by create_task_from_strategy; a bare
        # create_task would leave this key unset.
        assert task.metadata["plan_kind"] == "local"
        await runtime.stop()

    asyncio.run(run())


@pytest.mark.parametrize("instruction", ["", "   "])
def test_planning_rejects_an_empty_requirement(tmp_path: Path, instruction: str) -> None:
    """Invalid owned input cannot produce an unscoped fallback success."""
    settings = _settings(tmp_path)
    root = _git_workspace(tmp_path)

    async def run() -> None:
        runtime = create_runtime(settings)
        runtime.models.register(_StepModel(), default=True)
        workspace = await runtime.open_workspace(root)

        try:
            with pytest.raises(PermissionError, match="analysis_publication_unavailable"):
                await runtime.create_task_planned(workspace.id, instruction, **_host_inputs(root))
            await _assert_uncertain_preparation(runtime)
        finally:
            await runtime.stop()

    asyncio.run(run())
