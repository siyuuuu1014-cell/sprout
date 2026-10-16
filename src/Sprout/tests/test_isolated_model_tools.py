from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.file_broker import FileBroker
from Sprout.execution.isolated_model_tools import (
    IsolatedModelPathGuard,
    IsolatedModelTool,
    public_acceptance,
)
from Sprout.execution.models import SandboxRef
from Sprout.execution.sandbox_tool import (
    SandboxApplyPatchTool,
    SandboxListTool,
    SandboxReadTool,
    SandboxSearchTool,
)
from Sprout.orchestration.models import ExecutionNode, NodeType
from Sprout.runtime.nodes import NodeExecutor
from Sprout.security.engine import PolicyEngine
from Sprout.skills.models import Skill
from Sprout.task.models import Task
from Sprout.tests.test_coding_profile import plan
from Sprout.workspace.models import Workspace


def fixture(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/oracle.py").write_text("hidden-oracle-answer = 42\n")
    (tmp_path / "app.py").write_text("value = 1\n")
    sandbox = SandboxRef(id="sandbox", kind="git_worktree", root=tmp_path)
    task = Task(metadata={"replay_plan": plan(tmp_path)})
    return sandbox, task, IsolatedModelPathGuard(sandbox, task)


@pytest.mark.parametrize("path", ["tests/oracle.py", "TESTS/oracle.py", "tests\\oracle.py",
    "./tests/oracle.py", "tests/../app.py", "../app.py", "/app.py", "app.py:stream",
    ".git/config", ".workbuddy/metadata.db", ".env", "tests /oracle.py"])
async def test_frozen_private_and_alias_paths_block_before_delegate(tmp_path, path):
    _, _, guard = fixture(tmp_path)
    tool = SimpleNamespace(spec=SimpleNamespace(name="sandbox_read_file"), invoke=AsyncMock())
    output = await IsolatedModelTool(tool, guard).invoke({"path": path})
    assert output.ok is False
    tool.invoke.assert_not_called()


async def test_readable_source_is_preserved_and_list_search_do_not_read_oracles(tmp_path):
    sandbox, _, guard = fixture(tmp_path)
    broker = FileBroker(PolicyEngine())
    read = IsolatedModelTool(SandboxReadTool(sandbox, broker), guard)
    assert (await read.invoke({"path": "app.py"})).content == "value = 1\n"
    listing = await IsolatedModelTool(SandboxListTool(sandbox), guard).invoke({})
    assert "tests" not in listing.content and "app.py" in listing.content
    search = await IsolatedModelTool(SandboxSearchTool(sandbox), guard).invoke(
        {"query": "hidden-oracle-answer"})
    assert "hidden-oracle-answer" not in search.content


async def test_multi_file_patch_checks_all_targets_before_any_write(tmp_path):
    sandbox, _, guard = fixture(tmp_path)
    patch = ("--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
             "--- a/tests/oracle.py\n+++ b/tests/oracle.py\n@@ -1 +1 @@\n"
             "-hidden-oracle-answer = 42\n+hidden-oracle-answer = 0\n")
    tool = IsolatedModelTool(SandboxApplyPatchTool(sandbox, FileBroker(PolicyEngine())), guard)
    assert (await tool.invoke({"patch": patch})).ok is False
    assert (tmp_path / "app.py").read_text() == "value = 1\n"
    assert "42" in (tmp_path / "tests/oracle.py").read_text()


def test_node_boundary_filters_cached_read_resources_and_keeps_ordinary_mode(tmp_path):
    sandbox, task, _ = fixture(tmp_path)
    executor = object.__new__(NodeExecutor)
    executor._isolated_coding_required = True
    resources = ({"path": "app.py", "content": "code"},
                 {"path": "tests/oracle.py", "content": "hidden-oracle-answer"})
    _, filtered = executor._model_access(task, sandbox, [], resources)
    assert filtered == resources[:1]
    executor._isolated_coding_required = False
    assert executor._model_access(task, sandbox, [], resources)[1] == resources


def test_public_acceptance_keeps_inputs_but_withholds_expected_output():
    output = public_acceptance({"cases": [{"id": "case", "target": "app:run", "args": [1],
                                          "expected_output": "hidden-oracle-answer"}]})
    assert output["cases"][0]["args"] == [1]
    assert "hidden-oracle-answer" not in str(output)


@pytest.mark.parametrize("commands", [False, True])
@pytest.mark.parametrize("restricted", [False, True])
async def test_agent_prompt_and_context_hide_oracles(tmp_path, commands, restricted):
    sandbox, task, _ = fixture(tmp_path)
    nodes = [ExecutionNode(type=NodeType.SANDBOX, metadata={"output": {
        "id": sandbox.id, "kind": sandbox.kind, "root": str(sandbox.root)}}),
        ExecutionNode(type=NodeType.READ, metadata={"output": [
            {"path": str(tmp_path / "app.py"), "content": "code", "decision": "allow"},
            {"path": str(tmp_path / "tests/oracle.py"), "content": "hidden-oracle-answer",
             "decision": "allow"}]}),
        ExecutionNode(type=NodeType.TEST_DESIGN, metadata={"output": {"acceptance_spec": {
            "cases": [{"id": "ok", "target": "app:run", "args": [1],
                       "expected_output": "hidden-oracle-answer"}]}}})]
    captured = {}

    def context_factory(**kwargs):
        captured["context"] = kwargs
        return SimpleNamespace(**kwargs)

    async def run(message, context):
        captured["message"] = message.content
        return SimpleNamespace(content="done", metadata={})

    executor = object.__new__(NodeExecutor)
    executor._isolated_coding_required = True
    executor._coding_commands = SimpleNamespace(execute=AsyncMock()) if commands else None
    executor._metadata = SimpleNamespace(list_execution_nodes=AsyncMock(return_value=nodes))
    executor._file_broker = FileBroker(PolicyEngine())
    executor._contexts = SimpleNamespace(build_node_context=context_factory)
    executor._router = SimpleNamespace(route=lambda message: SimpleNamespace(run=run))
    executor._skills = SimpleNamespace(list=lambda: {
        "procedure": Skill(name="procedure", version="1", instructions="Read evidence.",
                           trust="trusted", origin=str(tmp_path)),
        "untrusted": Skill(name="untrusted", version="1", instructions="Not approved.")})
    await executor._agent(ExecutionNode(task_id=task.id, metadata={
        "tool_names": ["isolated_coding_command"]} if restricted else {}),
                          Workspace(id="workspace", root=tmp_path), task)
    assert "hidden-oracle-answer" not in captured["message"]
    assert "hidden-oracle-answer" not in str(captured["context"]["metadata"]["resources"])
    assert ("isolated_coding_command" in captured["context"]["tools"]) is commands
    assert ("skill_view" in captured["context"]["tools"]) is not restricted
    assert list(captured["context"]["skills"]) == ([] if restricted else ["procedure"])
    if not restricted:
        assert captured["context"]["skills"]["procedure"].origin == ""
        tool = captured["context"]["tools"]["skill_view"]
        assert (await tool.invoke({"name": "procedure"})).ok
        assert not (await tool.invoke({"name": "procedure", "path": "app.py"})).ok
