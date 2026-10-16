import pytest

from Sprout.execution.file_broker import FileBroker
from Sprout.execution.isolated_model_tools import IsolatedModelPathGuard, IsolatedModelTool
from Sprout.execution.models import SandboxRef
from Sprout.execution.sandbox_tool import SandboxReadTool, SandboxWriteTool
from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.task.models import Task
from Sprout.tests.live_coding_policy import LiveCodingPolicy
from Sprout.tests.test_coding_profile import plan
from Sprout.tests.test_isolated_coding_temporal_integration import CodingFixturePolicy
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry


def scoped_executor(tmp_path, policy):
    sandbox = SandboxRef(id="trial", kind="git_worktree", root=tmp_path)
    task = Task(metadata={"replay_plan": plan(tmp_path)})
    guard = IsolatedModelPathGuard(sandbox, task)
    broker = FileBroker(policy)
    tools = [SandboxReadTool(sandbox, broker), SandboxWriteTool(sandbox, broker)]
    scoped = {tool.spec.name: IsolatedModelTool(tool, guard) for tool in tools}
    return ToolExecutor(tools=ToolRegistry(), policy_engine=policy,
                        scoped=scoped, scoped_only=True)


async def test_old_fixture_reproduces_model_file_tool_denial(tmp_path):
    (tmp_path / "app.py").write_text("value = 0\n", encoding="utf-8")
    executor = scoped_executor(tmp_path, CodingFixturePolicy())
    result = await executor.execute("sandbox_read_file", {"path": "app.py"})
    assert not result.ok and "security policy" in result.error


async def test_model_can_read_edit_and_create_tests_through_actual_executor(tmp_path):
    (tmp_path / "app.py").write_text("def add(a,b): return 0\n", encoding="utf-8")
    executor = scoped_executor(tmp_path, LiveCodingPolicy())
    read = await executor.execute("sandbox_read_file", {"path": "app.py"})
    assert read.ok and "return 0" in read.content
    write = await executor.execute("sandbox_write_file", {
        "path": "app.py", "content": "def add(a,b): return a+b\n"})
    assert write.ok
    created = await executor.execute("sandbox_write_file", {
        "path": "test_generated.py", "content": "from app import add\n"})
    assert created.ok
    assert "return a+b" in (tmp_path / "app.py").read_text()


@pytest.mark.parametrize("path", ["tests/oracle.py", ".env", ".git/config", "../escape.py"])
async def test_dispatch_grant_does_not_bypass_path_guard(tmp_path, path):
    executor = scoped_executor(tmp_path, LiveCodingPolicy())
    result = await executor.execute("sandbox_write_file", {"path": path, "content": "no"})
    assert not result.ok
    assert not (tmp_path / "tests/oracle.py").exists()


def test_arbitrary_tool_does_not_receive_dispatch_grant():
    decision = LiveCodingPolicy().decide(ActionRequest(action=ActionType.TOOL_INVOKE,
        arguments={"tool_name": "run_shell"}))
    assert decision.decision is not AccessDecision.ALLOW
