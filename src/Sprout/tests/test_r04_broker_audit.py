"""R04 red contracts: all host/process/network effects replaced by spies.

These failures are intentional review evidence, not an implementation or a
claim that the existing restricted node allowlist is already bypassed.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from Sprout.execution.database_broker import DatabaseBroker
from Sprout.execution.git_broker import GitBroker
from Sprout.execution.models import GitResult, ProcessResult
from Sprout.mcp.adapter.tool import MCPToolAdapter
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.security.policy import SecurityPolicy
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.tools import system_tools
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry
from Sprout.tools.result import ToolExecutionUnconfirmed, ToolResult
from Sprout.tools.system_tools import CliRunTool, GitInspectTool, GitWriteTool, SkillScriptTool
from Sprout.workspace.models import Workspace, WorkspaceKind


@pytest.fixture
def host_calls(monkeypatch):
    calls = []

    async def command(*args, **kwargs):
        calls.append((args, kwargs))
        return 0, "fake", ""

    async def git(args):
        calls.append(tuple(args))
        return ToolResult.success("fake")

    monkeypatch.setattr(system_tools, "_run_command", command)
    monkeypatch.setattr(system_tools, "_run_git", git)
    monkeypatch.setattr(system_tools.shutil, "which", lambda name: "test-only-program")
    return calls


async def test_cli_without_process_broker_must_not_start_host(tmp_path, host_calls):
    result = await CliRunTool(allowed_roots=(tmp_path,)).invoke({
        "command": "test-only-program", "args": [], "cwd": str(tmp_path),
    })
    assert not host_calls
    assert not result.ok


class DeniedGit:
    def __init__(self):
        self.calls = []

    def __getattr__(self, operation):
        async def denied(*args, **kwargs):
            self.calls.append((operation, args, kwargs))
            return GitResult(action=operation, allowed=False, reason="test lower DENY")
        return denied


@pytest.mark.parametrize("operation", sorted(GitWriteTool._OPERATIONS))
async def test_every_git_write_operation_obeys_injected_broker(
    tmp_path, host_calls, operation,
):
    broker = DeniedGit()
    result = await GitWriteTool(git_broker=broker).invoke({
        "operation": operation, "repo": str(tmp_path), "files": ["note.txt"],
        "message": "test", "branch": "topic", "ref": "HEAD", "remote": "origin",
        "tag": "v-test", "stash": "stash@{0}",
        "url": "https://example.invalid/repo", "directory": str(tmp_path / "clone"),
    })
    assert not host_calls, operation
    assert not result.ok
    assert len(broker.calls) == 1


@pytest.mark.parametrize("operation", sorted(GitInspectTool._OPERATIONS))
async def test_every_git_inspect_operation_obeys_injected_broker(
    tmp_path, host_calls, operation,
):
    broker = DeniedGit()
    result = await GitInspectTool(git_broker=broker).invoke({
        "operation": operation, "repo": str(tmp_path), "ref": "HEAD",
    })
    assert not host_calls, operation
    assert not result.ok
    assert len(broker.calls) == 1


class AllowPolicy:
    def decide(self, request):
        return PolicyDecision(AccessDecision.ALLOW, "test outer allows")


async def test_git_broker_rejects_option_like_remote_before_subprocess(tmp_path, monkeypatch):
    calls = []

    async def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return 0, "fake", ""

    monkeypatch.setattr("Sprout.execution.git_broker.run_capture", capture)
    result = await GitBroker(AllowPolicy()).push(
        Workspace(id="test", root=tmp_path, kind=WorkspaceKind.GIT_REPOSITORY), "--all",
    )
    assert not calls
    assert not result.allowed


async def test_git_broker_explicitly_constrains_hook_execution(tmp_path, monkeypatch):
    calls = []

    async def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return 0, "fake", ""

    monkeypatch.setattr("Sprout.execution.git_broker.run_capture", capture)
    await GitBroker(AllowPolicy()).commit(
        Workspace(id="test", root=tmp_path, kind=WorkspaceKind.GIT_REPOSITORY), "test",
        files=("note.txt",),
    )
    # Until a separately trusted hook capability exists, host hooks cannot be
    # inferred safe from repository membership or an outer commit approval.
    assert all(any(str(arg).startswith("core.hooksPath=") for arg in args)
               for args, _ in calls)


class Remote:
    name = "test_remote"

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error:
            raise self.error
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text="fake")])


def remote_tool(connection):
    return MCPToolAdapter().to_tool(connection, SimpleNamespace(
        name="operation", description="Unclassified remote operation",
        inputSchema={"type": "object"},
    ))


@pytest.mark.parametrize("source", ["interactive", "cron"])
async def test_unclassified_remote_requires_trusted_risk_before_call(source):
    connection = Remote()
    registry = ToolRegistry()
    registry.register(remote_tool(connection))
    result = await ToolExecutor(
        registry, approvals=ApprovalManager(MemoryOperationalStore()),
    ).execute("test_remote_operation", {}, task_id="test-task", source=source)
    assert not connection.calls
    assert not result.ok


async def test_remote_timeout_does_not_prove_no_effects():
    connection = Remote(TimeoutError("test timeout"))
    with pytest.raises(ToolExecutionUnconfirmed):
        await remote_tool(connection).invoke({})


@pytest.mark.parametrize("source", ["cron", "automation"])
async def test_existing_unattended_high_risk_gate_blocks_before_host(
    tmp_path, host_calls, source,
):
    registry = ToolRegistry()
    registry.register(CliRunTool(allowed_roots=(tmp_path,)))
    result = await ToolExecutor(
        registry, approvals=ApprovalManager(MemoryOperationalStore()),
    ).execute("cli_tool_run", {"command": "test-only-program", "cwd": str(tmp_path)},
              task_id="test-task", source=source)
    assert not result.ok
    assert not host_calls


@pytest.mark.parametrize("outcome", ["deny", "timeout", "cancel"])
async def test_lower_broker_refusal_or_interruption_never_falls_back(
    tmp_path, host_calls, outcome,
):
    async def run(*args, **kwargs):
        if outcome == "timeout":
            raise TimeoutError("test")
        if outcome == "cancel":
            raise asyncio.CancelledError
        return ProcessResult(command=(), allowed=False, error="test lower DENY")

    registry = ToolRegistry()
    registry.register(CliRunTool(allowed_roots=(tmp_path,),
                                 process_broker=SimpleNamespace(run=run)))
    executor = ToolExecutor(registry, policy=SecurityPolicy(
        always_allow=frozenset({"cli_tool_run"})))
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await executor.execute("cli_tool_run", {"command": "test-only-program",
                                                   "cwd": str(tmp_path)})
    else:
        result = await executor.execute("cli_tool_run", {"command": "test-only-program",
                                                       "cwd": str(tmp_path)})
        assert not result.ok
    assert not host_calls


async def test_skill_script_has_no_unbrokered_host_execution(tmp_path, host_calls, monkeypatch):
    async def fake_ensure():
        return None

    monkeypatch.setattr("Sprout.orchestration.terminal.ensure.ensure_temporal_async", fake_ensure)
    (tmp_path / "skill").mkdir()
    (tmp_path / "skill/run.py").write_text("# never executed\n", encoding="utf-8")
    result = await SkillScriptTool(tmp_path).invoke({"skill": "skill", "script": "run.py"})
    assert not host_calls
    assert not result.ok


@pytest.mark.parametrize("tool,arguments", [
    (system_tools.CliDetectTool, {"command": "test-only-program"}),
    (system_tools.GrepCheckTool, {"pattern": "test", "path": "."}),
])
async def test_low_risk_label_is_not_process_authority(tool, arguments, host_calls):
    result = await tool().invoke(arguments)
    assert not host_calls
    assert not result.ok


@pytest.mark.parametrize("sql", [
    "CREATE TABLE test_only (value TEXT)", "INSERT INTO test_only VALUES ('test')",
    "DELETE FROM test_only", "PRAGMA journal_mode=WAL",
])
async def test_database_read_action_cannot_dispatch_mutating_sql(tmp_path, monkeypatch, sql):
    calls = []

    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchall=lambda: []),
                               close=lambda: None)

    monkeypatch.setattr("Sprout.execution.database_broker.sqlite3.connect", connect)
    result = await DatabaseBroker(PolicyEngine()).query(
        Workspace(id="test", root=tmp_path, kind=WorkspaceKind.LOCAL_DIRECTORY),
        tmp_path / "never-opened.sqlite", sql,
    )
    assert not calls
    assert not result.allowed


async def test_download_without_network_broker_has_no_effect(tmp_path, monkeypatch):
    calls = []

    async def chunks():
        yield b"test-only-bytes"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            calls.append(args)
            yield SimpleNamespace(is_redirect=False, raise_for_status=lambda: None,
                                  aiter_bytes=chunks)

    async def check(url):
        return SimpleNamespace(allowed=True)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(system_tools.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await system_tools.CliDownloadTool(guard=SimpleNamespace(acheck=check)).invoke({
        "url": "https://example.invalid/program", "destination": "download/program",
    })
    assert not calls
    assert not (tmp_path / "download").exists()
    assert not result.ok
