"""Local CLI diagnostics: opt-in, isolated data, and fail-closed effects."""
import sys

import pytest

from Sprout.config.settings import Settings
from Sprout.tests.test_r04_b4_cli_auto import (
    automatic as automatic,
)
from Sprout.tests.test_r04_b4_cli_auto import (
    captured as captured,
)
from Sprout.tests.test_r04_b4_cli_auto import (
    cli as cli,
)
from Sprout.tests.test_r04_b4_cli_auto import (
    rig as rig,
)


@pytest.mark.parametrize("entry", ["one_shot", "no_tty", "resume", "temporal"])
def test_diagnostics_rejects_noninteractive_or_remote_entry(monkeypatch, entry):
    from Sprout.security.local_diagnostics import prepare

    monkeypatch.setenv("SPROUT_DEV_NO_HUMAN_APPROVAL", "1")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: entry != "no_tty")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    if entry == "temporal":
        monkeypatch.setenv("TEMPORAL_HOST", "127.0.0.1:7233")
    else:
        monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    with pytest.raises(PermissionError):
        prepare(Settings(), message="hello" if entry == "one_shot" else None,
                session="old-session" if entry == "resume" else None)


def test_diagnostics_disabled_by_default(monkeypatch):
    from Sprout.security.local_diagnostics import prepare

    monkeypatch.delenv("SPROUT_DEV_NO_HUMAN_APPROVAL", raising=False)
    assert prepare(Settings(), message=None, session=None) is None


def test_diagnostics_data_is_fresh_and_does_not_mutate_settings(monkeypatch, tmp_path):
    from Sprout.security.local_diagnostics import prepare

    monkeypatch.setenv("SPROUT_DEV_NO_HUMAN_APPROVAL", "1")
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    settings = Settings()
    original = settings.storage.metadata
    a = prepare(settings, message=None, session=None)
    b = prepare(settings, message=None, session=None)
    assert a.root != b.root
    assert settings.storage.metadata == original
    assert a.root.as_posix() in a.settings.storage.metadata
    assert a.root.as_posix() in a.settings.storage.session
    assert a.root.as_posix() in a.settings.storage.operational
    assert a.settings.security.require_approval
    assert a.settings.mcp.clients == ()


@pytest.fixture
def diagnostic():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def create():
        from Sprout.gateway.local_identity import current_cli_principal
        from Sprout.message.models import Message
        from Sprout.runtime.factory import create_runtime
        from Sprout.runtime.lifecycle import managed
        from Sprout.security.local_diagnostics import LocalCLIDiagnostics

        settings = Settings()
        settings.model.provider = "echo"
        mode = LocalCLIDiagnostics(settings)
        runtime = create_runtime(mode.settings, local_diagnostics=mode)
        principal = current_cli_principal()
        async with managed(runtime), mode.foreground(runtime):
            session = await runtime._sessions.resolve(
                Message("", channel="cli"), principal=principal)
            await mode.bind(runtime, session.id, principal)
            yield mode, runtime, session, principal
    return create


async def test_diagnostic_host_command_denied_before_spawn_or_pending(diagnostic, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    async with diagnostic() as (mode, runtime, session, principal):
        spawn = AsyncMock(side_effect=AssertionError("host command launched"))
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        # Exercise the production tool executor through the registered agent.
        from Sprout.message.models import Message

        agent = runtime._router.route(Message("hi"))
        executor = agent.executor._tools
        result = await executor.execute("cli_tool_run", {"command": sys.executable, "args": ["-V"]},
            requested_by=principal.user_id, session_id=session.id, source="cli")
        assert not result.ok
        assert "Diagnostic" in result.to_text()
        assert not await runtime.approvals.store.list_approvals()
        spawn.assert_not_awaited()


async def test_bound_diagnostic_runs_host_command_without_approval(
    diagnostic, tmp_path, monkeypatch,
):
    from Sprout.message.models import Message
    from Sprout.security.approval import _CAPABILITY_TURN

    monkeypatch.chdir(tmp_path)
    async with diagnostic() as (_, runtime, session, principal):
        workspace = await runtime.open_workspace(tmp_path)
        await runtime._remember_session_workspace(session.id, workspace.id)
        owned = await runtime._sessions.require_owner(session.id, principal)
        turn = await runtime._start_capability_turn(owned, principal)
        token = _CAPABILITY_TURN.set(turn)
        try:
            executor = runtime._router.route(Message("hi")).executor._tools
            result = await executor.execute(
                "cli_tool_run",
                {"command": sys.executable, "args": [
                    "-c", "from pathlib import Path; "
                    "Path('probe.txt').write_text('ok'); print('done')",
                ]},
                requested_by=principal.user_id, session_id=session.id,
            )
            assert result.ok, result.to_text()
            assert "done" in result.to_text()
            assert (tmp_path / "probe.txt").read_text() == "ok"
            assert not await runtime.approvals.store.list_approvals()
        finally:
            _CAPABILITY_TURN.reset(token)


async def test_diagnostic_direct_broker_host_command_is_denied(diagnostic, tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from Sprout.workspace.models import Workspace

    async with diagnostic() as (_, runtime, _, principal):
        spawn = AsyncMock(side_effect=AssertionError("host command launched"))
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        workspace = Workspace(id="probe", root=tmp_path)
        result = await runtime._process_broker.run(
            workspace, (sys.executable, "-V"), actor=principal)
        assert not result.allowed and not result.started and not result.approval_id
        spawn.assert_not_awaited()


async def test_environment_alone_does_not_enable_web_or_runtime(monkeypatch):
    from Sprout.runtime.factory import create_runtime
    from Sprout.runtime.lifecycle import managed
    from Sprout.security.local_diagnostics import DiagnosticApprovals, LocalCLIDiagnostics

    settings = Settings()
    settings.model.provider = "echo"
    isolated = LocalCLIDiagnostics(settings).settings
    monkeypatch.setenv("SPROUT_DEV_NO_HUMAN_APPROVAL", "1")
    runtime = create_runtime(isolated)
    async with managed(runtime):
        assert not isinstance(runtime.approvals, DiagnosticApprovals)
        record = await runtime.approvals.request("cli_tool_run", {"command": "python"},
                                               source="cli", session_id="normal")
        assert record.status == "pending"


@pytest.mark.parametrize("case", [
    "directory", "file", "denied", "identity", "filesystem", "budget", "slash", "outside",
])
async def test_diagnostic_read_analysis_keeps_scope_and_deny(automatic, monkeypatch, case):
    from Sprout.execution.file_access import FileBoundaryError
    from Sprout.security.local_diagnostics import DiagnosticApprovals, LocalCLIDiagnostics

    cli = automatic
    runtime = cli.rig.runtime
    mode = LocalCLIDiagnostics(Settings())
    manager = DiagnosticApprovals(runtime.storage.operational, diagnostics=mode)
    runtime._approvals = manager
    runtime._local_cli_diagnostics = mode
    cli.rig.executor.approvals = manager
    runtime._file_broker._approvals = manager
    cli.state.inputs = ["分析 src/main.py"] if case == "file" else ["分析当前项目"]
    if case == "outside":
        cli.state.inputs = ["分析 ../outside.py"]
    if case == "slash":
        cli.state.inputs = ["/db", "/apply", "/task forbidden"]
    cli.state.answers = []  # Any remaining human confirmation is a failure.
    if case == "denied":
        from Sprout.security.access import AccessDecision
        from Sprout.security.layered_policy import LayeredPolicyEngine, PolicyLayer, PolicyRule

        policy = LayeredPolicyEngine(workspace=PolicyLayer("deny", (
            PolicyRule(action="directory.list", decision=AccessDecision.DENY),)))
        runtime._policy_engine = policy
        runtime._file_broker._policy = policy
    if case == "filesystem":
        async def refused(*args):
            raise FileBoundaryError("secure_open_refused:5")
        monkeypatch.setattr("Sprout.execution.file_access.directory_entries", refused)
    if case == "budget":
        from Sprout.execution.file_snapshot import DirectoryReadLimits

        original = runtime._file_broker.enumerate_readable
        async def bounded(*args, **kwargs):
            return await original(*args, **kwargs, limits=DirectoryReadLimits(max_entries=1))
        monkeypatch.setattr(runtime._file_broker, "enumerate_readable", bounded)
    async with mode.foreground(runtime):
        await mode.bind(runtime, cli.rig.session.id, cli.rig.principal)
        if case == "identity":
            mode.session_id = "foreign-session"
        await cli.run()
        records = await manager.store.list_approvals()
        assert not any(record.status == "pending" for record in records)
    success = case in {"directory", "file"}
    marker = "CLI_PROJECT_MAIN_RESULT" if case == "file" else "Indexed counts"
    assert any(marker in text for text in cli.state.display) == success
    assert bool(cli.captured.opens) == success
    if case == "filesystem":
        assert any("directory_access_denied" in text for text in cli.state.display)
    assert not any(record.status == "approved" for record in await manager.store.list_approvals())


async def test_diagnostic_chat_has_no_human_approval(diagnostic):
    from Sprout.message.models import Message

    async with diagnostic() as (_, runtime, session, principal):
        reply = await runtime.handle(Message("你好", channel="cli", session_id=session.id),
                                     principal=principal)
        assert reply.content
        assert not reply.metadata.get("approval_required")
        assert not await runtime.approvals.store.list_approvals()


async def test_diagnostic_scope_is_not_inherited_by_background_task(diagnostic):
    import asyncio

    async with diagnostic() as (mode, _, _, _):
        assert mode.active()
        async def background():
            with pytest.raises(PermissionError):
                await mode.require()
        await asyncio.create_task(background())


async def test_diagnostic_unverified_isolation_cannot_auto_approve(diagnostic):
    from types import SimpleNamespace

    from Sprout.security.local_diagnostics import coding_approval

    async with diagnostic() as (_, runtime, session, principal):
        scope = SimpleNamespace(runtime=runtime, task=SimpleNamespace(actor=principal),
                                session_id=session.id)
        with pytest.raises(PermissionError, match="isolated coding binding"):
            with coding_approval(runtime.approvals, object(), scope):
                pytest.fail("unverified tool accepted")
        assert not await runtime.approvals.store.list_approvals()


@pytest.mark.parametrize("deny", [False, True])
async def test_workspace_request_uses_existing_policy_and_progress(diagnostic, deny):
    from dataclasses import replace

    from Sprout.message.models import Message
    from Sprout.security.policy import SecurityPolicy

    async with diagnostic() as (mode, runtime, session, principal):
        lines = []
        mode.progress = lines.append
        agent = runtime._router.route(Message("hi"))
        executor = agent.executor._tools
        if deny:
            executor = replace(executor, policy=SecurityPolicy(
                always_deny=frozenset({"request_workspace"})))
        result = await executor.execute("request_workspace", {"reason": "test"},
            requested_by=principal.user_id, session_id=session.id, source="cli")
        assert result.workspace_request is not deny
        assert not await runtime.approvals.store.list_approvals()
        assert lines == (["[Tool] request_workspace: DENIED"] if deny else [
            "[Tool] request_workspace: started", "[Tool] request_workspace: success",
        ])


@pytest.mark.parametrize("outcome", ["success", "directory", "deny", "outside"])
async def test_scoped_read_authorization_and_file_broker_result_are_separate(rig, outcome):
    from Sprout.execution.models import SandboxRef
    from Sprout.execution.sandbox_tool import SandboxReadTool
    from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
    from Sprout.security.local_diagnostics import DiagnosticApprovals, LocalCLIDiagnostics
    from Sprout.security.policy import SecurityPolicy
    from Sprout.tests.test_r04_pa2_b1_binding import node_scope

    runtime = rig.runtime
    await runtime._remember_session_workspace(rig.session.id, rig.workspace.id)
    (rig.root / "src").mkdir(exist_ok=True)
    (rig.root / "src" / "probe.py").write_text("diagnostic file probe", encoding="utf-8")
    sandbox = SandboxRef("probe", "fixture", rig.root)
    await rig.storage.metadata.save_execution_node(ExecutionNode(
        task_id=rig.task.id, type=NodeType.SANDBOX, status=NodeStatus.COMPLETED,
        metadata={"output": {"id": sandbox.id, "root": str(sandbox.root)}}))
    mode = LocalCLIDiagnostics(Settings())
    manager = DiagnosticApprovals(runtime.storage.operational, diagnostics=mode)
    runtime._approvals = manager
    runtime._local_cli_diagnostics = mode
    lines = []
    mode.progress = lines.append
    tool = SandboxReadTool(
        sandbox, runtime._file_broker, scope=rig.task.delegation_scope, task_id=rig.task.id)
    name = tool.spec.name
    executor = rig.executor.with_scoped_tools({name: tool}, strict=True)
    executor.approvals = manager
    executor.policy = SecurityPolicy(
        always_deny=frozenset({name}) if outcome == "deny" else frozenset(),
        allow_medium_risk=False,
    )
    # Exercise the human-approval tier for this exact built-in FileBroker tool.
    from dataclasses import replace

    tool.spec = replace(tool.spec, risk_level="high")
    path = {"directory": "src", "outside": "outside.py"}.get(outcome, "src/probe.py")
    async with mode.foreground(runtime):
        await mode.bind(runtime, rig.session.id, rig.principal)
        with node_scope(rig) as scope:
            runtime._node_tool_scopes[tool] = scope
            result = await executor.execute(name, {"path": path})
        assert not result.approval_id
        if outcome in {"deny", "outside"}:
            assert not result.ok
            assert lines == [f"[Tool] {name}: DENIED"]
            assert not await manager.store.list_approvals()
        else:
            assert result.ok is (outcome == "success")
            assert lines == [f"[Authorization] LOCAL-EXECUTION: {name}", f"[Tool] {name}: started",
                             f"[Tool] {name}: {outcome if result.ok else 'failed'}"]
            assert not await manager.store.list_approvals()
            if result.ok:
                assert result.content == "diagnostic file probe"



@pytest.mark.parametrize("tool", ["process_run", "file.apply"])
async def test_diagnostic_exact_git_approval_is_bound(diagnostic, monkeypatch, tool):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.security.approval import ApprovalStatus
    from Sprout.security.local_diagnostics import exact_approval

    async with diagnostic() as (mode, runtime, session, principal):
        task = SimpleNamespace(id="git-task", actor=principal, source="cli",
                               metadata={"session_id": session.id})
        monkeypatch.setattr(runtime, "get_task", AsyncMock(return_value=task))
        progress = []
        mode.progress = progress.append
        manager = runtime.approvals
        identity = dict(task_id=task.id, requested_by=principal.user_id,
                        session_id=session.id, source="cli", resource_scope="scope")
        args = {"fingerprint": "exact"}
        with pytest.raises(PermissionError, match="refuses this effect"):
            await manager.request(tool, args, **identity)
        with exact_approval(manager, task.id, tool, args, "scope"):
            with pytest.raises(PermissionError, match="refuses this effect"):
                await manager.request(tool, {"fingerprint": "changed"}, **identity)
            record = await manager.request(tool, args, **identity)
        if tool == "file.apply":
            assert record.status is ApprovalStatus.PENDING
            record = await manager.decide(record.id, True, principal=principal)
        assert record.status is ApprovalStatus.APPROVED
        assert progress == [f"[Approval] AUTO-APPROVED: {tool}"]
