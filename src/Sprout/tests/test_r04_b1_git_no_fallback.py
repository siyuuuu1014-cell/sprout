"""B1 dispatch contracts; every final process/Git effect is mocked."""

import ast
import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.models import GitResult
from Sprout.tools import system_tools
from Sprout.tools.result import ToolResult
from Sprout.tools.system_tools import GitInspectTool, GitWriteTool

SUPPORTED = {GitInspectTool: {"status", "log", "branch", "diff"},
             GitWriteTool: {"add", "commit", "pull"}}
CASES = [(tool, op) for tool in SUPPORTED for op in sorted(tool._OPERATIONS)]
DELEGATED = [(tool, op) for tool, ops in SUPPORTED.items() for op in sorted(ops)]
UNSUPPORTED = [(tool, op) for tool, op in CASES if op not in SUPPORTED[tool]]


@pytest.fixture(autouse=True)
def no_host(monkeypatch):
    spies = [AsyncMock(side_effect=AssertionError("Host effect forbidden")) for _ in range(3)]
    monkeypatch.setattr(system_tools, "_run_git", spies[0])
    monkeypatch.setattr(system_tools, "_run_command", spies[1])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spies[2])
    yield
    for spy in spies:
        spy.assert_not_awaited()


def arguments(tmp_path, operation):
    return {"operation": operation, "repo": str(tmp_path), "message": "test",
            "files": ["note.txt"], "limit": 3, "ref": "--exec=forbidden",
            "branch": "--force", "remote": "ext::forbidden", "url": "ext::forbidden",
            "tag": "test", "trusted_local": True,
            "_sprout_context": {"approval_granted": True, "source": "cli"}}


@pytest.mark.parametrize("tool,operation", CASES)
async def test_missing_broker_refuses_all_operations(tmp_path, tool, operation):
    result = await tool().invoke(arguments(tmp_path, operation))
    assert not result.ok
    assert "GitBroker" in result.to_text()


@pytest.mark.parametrize("tool,operation", UNSUPPORTED)
async def test_unsupported_operation_never_calls_even_available_broker(tmp_path, tool, operation):
    methods = {op: AsyncMock() for _, op in CASES}
    instance = tool(git_broker=SimpleNamespace(**methods))
    result = await instance.invoke(arguments(tmp_path, operation))
    assert not result.ok
    assert "not supported" in result.to_text()
    for method in methods.values():
        method.assert_not_called()


@pytest.mark.parametrize("tool,operation", DELEGATED)
@pytest.mark.parametrize("capability", ["absent", None, False])
async def test_missing_or_noncallable_capability_refuses(tmp_path, tool, operation, capability):
    broker = SimpleNamespace(**({} if capability == "absent" else {operation: capability}))
    result = await tool(git_broker=broker).invoke(arguments(tmp_path, operation))
    assert not result.ok
    assert "capability" in result.to_text()


@pytest.mark.parametrize("tool,operation", DELEGATED)
@pytest.mark.parametrize("outcome", ["allow", "deny", "pending", "failed",
                                    "timeout", "exception", "cancel"])
async def test_supported_delegates_once_without_retry(tmp_path, tool, operation, outcome):
    errors = {"timeout": TimeoutError, "exception": RuntimeError,
              "cancel": asyncio.CancelledError}
    method = AsyncMock()
    if outcome in errors:
        method.side_effect = errors[outcome]("mock broker interruption")
    else:
        method.return_value = GitResult(action=operation, allowed=outcome == "allow",
                                        output="mock", reason=outcome)
    instance = tool(git_broker=SimpleNamespace(**{operation: method}))
    if outcome in errors:
        with pytest.raises(errors[outcome]):
            await instance.invoke(arguments(tmp_path, operation))
    else:
        result = await instance.invoke(arguments(tmp_path, operation))
        assert result.ok == (outcome == "allow")
    method.assert_awaited_once()
    args, kwargs = method.call_args
    assert args[0].root == tmp_path
    if operation == "log":
        assert args[1:] == (3,)
    elif operation == "add":
        assert args[1:] == (("note.txt",),)
    elif operation == "commit":
        assert args[1:] == ("test",)
        assert kwargs == {"files": ("note.txt",)}
    else:
        assert len(args) == 1 and not kwargs


@pytest.mark.parametrize("tool", [GitInspectTool, GitWriteTool])
def test_public_operations_preserved_and_no_host_calls_in_tool(tool):
    assert len(tool._OPERATIONS) == (10 if tool is GitInspectTool else 21)
    assert set(tool.spec.input_schema["properties"]["operation"]["enum"]) == tool._OPERATIONS
    tree = ast.parse(inspect.getsource(tool))
    calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert not any(isinstance(call, ast.Name) and call.id in
                   {"_run_git", "_run_command"} for call in calls)


def test_legacy_spy_helper_is_retained():
    assert callable(system_tools._run_git)
    assert callable(ToolResult.denied)
