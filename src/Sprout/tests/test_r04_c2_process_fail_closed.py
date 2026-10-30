"""C2-a: disabled native execution and discovery-only metadata, no real I/O."""

import asyncio
import inspect
import subprocess
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.security.approval import ApprovalManager
from Sprout.security.policy import SecurityPolicy
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.tools import system_tools
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry

KINDS = ("skill", "detect", "grep")


@pytest.fixture
def guard(monkeypatch, tmp_path):
    forbidden = Mock(side_effect=AssertionError("Unexpected external effect"))
    host = AsyncMock(side_effect=AssertionError("Unexpected native execution"))
    temporal = AsyncMock(side_effect=AssertionError("Unexpected Temporal probe"))

    class ForbiddenPopen:
        def __class_getitem__(cls, item):
            return cls

        def __new__(cls, *args, **kwargs):
            return forbidden(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", ForbiddenPopen)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", host)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", host)
    monkeypatch.setattr(system_tools, "_run_command", host)
    monkeypatch.setattr(system_tools, "_run_git", host)
    monkeypatch.setattr("Sprout.orchestration.terminal.ensure.ensure_temporal_async", temporal)
    lookup = Mock(return_value="fake-install")
    monkeypatch.setattr(system_tools.shutil, "which", lookup)

    def build(kind):
        if kind == "skill":
            return system_tools.SkillScriptTool(tmp_path), {"skill": "any", "script": "run.py"}
        if kind == "detect":
            return system_tools.CliDetectTool(), {"command": "test-program"}
        return system_tools.GrepCheckTool(), {"pattern": "--anything", "path": str(tmp_path)}

    yield build, lookup, host
    forbidden.assert_not_called()
    host.assert_not_called()
    temporal.assert_not_called()


def dispatcher(tool, **kwargs):
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry, **kwargs)


def assert_closed_or_metadata(kind, result):
    if kind == "detect":
        assert result.ok
        assert result.data["found"] is True
        assert result.data["version"] == "not_executed"
        assert result.data["version_executed"] is False
        assert "not trusted" in result.content
    else:
        assert not result.ok and not result.approval_id
        assert result.error == ("skill_script_execution_unavailable" if kind == "skill"
                                else "native_grep_execution_unavailable")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("source", ["interactive", "cron", "automation"])
async def test_direct_invocation_has_no_execution_authority(guard, kind, source):
    build, lookup, _ = guard
    tool, args = build(kind)
    args.update(force=True, trusted=True, approval_granted=True, source=source,
                _sprout_context={"trusted_local": True, "approval_granted": True})
    assert_closed_or_metadata(kind, await tool.invoke(args))
    if kind != "detect":
        lookup.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("source", ["interactive", "cron"])
async def test_executor_allow_cannot_enable_native_execution(guard, kind, source):
    build, _, _ = guard
    tool, args = build(kind)
    result = await dispatcher(tool, policy=SecurityPolicy(
        always_allow=frozenset({tool.spec.name}))).execute(tool.spec.name, args, source=source)
    assert_closed_or_metadata(kind, result)


@pytest.mark.parametrize("single_use", [True, False])
async def test_approved_skill_is_disabled_but_executor_consumption_is_disclosed(guard, single_use):
    build, _, _ = guard
    tool, args = build("skill")
    approvals = ApprovalManager(MemoryOperationalStore())
    record = await approvals.request(tool.spec.name, args, task_id="task", single_use=single_use)
    await approvals.decide(record.id, True, decided_by="operator", channel="cli")
    result = await dispatcher(tool, approvals=approvals).execute(
        tool.spec.name, args, task_id="task")
    assert_closed_or_metadata("skill", result)
    after = await approvals._store.get_approval(record.id)
    # Current Executor consumes before invoke. Do not claim zero consumption.
    assert after.status.value == ("consumed" if single_use else "approved")


@pytest.mark.parametrize("version_args", [["--version"], ["--help"], []])
@pytest.mark.parametrize("found", [True, False])
async def test_detect_queries_only_never_requests_version(guard, version_args, found):
    build, lookup, _ = guard
    lookup.return_value = "fake-install" if found else None
    tool, args = build("detect")
    result = await tool.invoke({**args, "version_args": version_args})
    assert result.ok and result.data["found"] is found
    lookup.assert_called_once_with("test-program")
    if found:
        assert result.data["version_executed"] is False
    else:
        assert "version" not in result.data


@pytest.mark.parametrize("kind", ["skill", "grep"])
async def test_unavailable_precedes_validation_or_filesystem_scan(guard, kind, monkeypatch):
    build, lookup, _ = guard
    tool, _ = build(kind)
    scan = Mock(side_effect=AssertionError("Unexpected filesystem scan"))
    # Construct Skill first: constructor resolves configured directory only.
    with monkeypatch.context() as paths:
        paths.setattr(system_tools.Path, "resolve", scan)
        paths.setattr(system_tools.Path, "rglob", scan)
        paths.setattr(system_tools.Path, "glob", scan)
        paths.setattr(system_tools.Path, "read_text", scan)
        result = await tool.invoke({})
    assert_closed_or_metadata(kind, result)
    scan.assert_not_called()
    lookup.assert_not_called()


@pytest.mark.parametrize("error", [TimeoutError(), asyncio.CancelledError(), OSError("fake")])
@pytest.mark.parametrize("kind", KINDS)
async def test_unreachable_process_errors_cannot_trigger_fallback(guard, kind, error):
    build, _, host = guard
    host.side_effect = error
    tool, args = build(kind)
    assert_closed_or_metadata(kind, await tool.invoke(args))


def test_grep_has_no_replacement_python_scanner():
    source = inspect.getsource(system_tools.GrepCheckTool.invoke)
    # A direct, unconditional unavailable return is the first statement.
    import ast
    import textwrap

    body = ast.parse(textwrap.dedent(source)).body[0].body
    assert isinstance(body[0], ast.Return)
    assert ast.literal_eval(body[0].value.args[0]) == "native_grep_execution_unavailable"
