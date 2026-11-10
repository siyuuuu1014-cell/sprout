"""Temporary foreground CLI diagnostics; never enabled by Runtime environment.

This replaces selected human decisions, not Policy, Scope or Broker checks.
Every launch uses fresh data. Only the owning foreground coroutine can issue
short-lived diagnostic approvals; detached workers cannot inherit this mode.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from Sprout.security.approval import (
    CODING_TASK_CAPABILITY,
    ApprovalManager,
    ApprovalStatus,
)

_CODING_REQUEST = ContextVar("local_cli_diagnostic_coding", default=None)
_EXACT_REQUEST = ContextVar("local_cli_diagnostic_exact", default=None)
foreground_progress = ContextVar("local_cli_foreground_progress", default=None)


def prepare(settings, *, message, session):
    """Called exclusively by the interactive chat command, before opening data."""
    if os.getenv("SPROUT_DEV_NO_HUMAN_APPROVAL") != "1":
        return None
    if (message is not None or session is not None or not sys.stdin.isatty()
            or not sys.stdout.isatty() or os.getenv("TEMPORAL_HOST")):
        raise PermissionError("Diagnostic mode requires a new local interactive CLI session")
    return LocalCLIDiagnostics(settings)


class LocalCLIDiagnostics:
    def __init__(self, settings):
        self.root = Path(tempfile.mkdtemp(prefix="sprout-cli-diagnostic-"))
        self.settings = deepcopy(settings)
        storage = self.settings.storage
        for lane in ("operational", "knowledge", "metadata", "session", "context", "usage"):
            setattr(storage, lane, "sqlite:///" + (self.root / (lane + ".db")).as_posix())
        storage.observations.dsn = storage.operational
        storage.cache, storage.vectors, storage.graph = "memory", "memory", "none"
        for key in ("blobs_dir", "trajectory_dir", "project_root"):
            setattr(storage, key, str(self.root / key))
        self.settings.memory.home = str(self.root / "memory")
        self.settings.skills_dir = str(self.root / "skills")
        self.settings.security.audit.path = str(self.root / "security.jsonl")
        self.settings.security.require_approval = True
        self.settings.mcp.clients = ()
        self.settings.evolution.enabled = False
        self.runtime = self.owner = self.principal = None
        self.session_id = ""
        self.records = set()
        self.progress = None

    def active(self):
        return self.owner is not None and self.owner is asyncio.current_task()

    async def bind(self, runtime, session_id, principal):
        if (not self.active() or runtime is not self.runtime
                or not principal.authenticated or principal.source != "cli"):
            raise PermissionError("Diagnostic foreground identity unavailable")
        owned = await runtime._sessions.require_owner(session_id, principal)
        if runtime._sessions.frozen_principal(owned) != principal:
            raise PermissionError("Diagnostic Session identity mismatch")
        self.session_id, self.principal = session_id, principal

    async def require(self):
        if not self.active() or self.principal is None or not self.session_id:
            raise PermissionError("Diagnostic mode is unavailable outside its foreground Session")
        owned = await self.runtime._sessions.require_owner(self.session_id, self.principal)
        if self.runtime._sessions.frozen_principal(owned) != self.principal:
            raise PermissionError("Diagnostic Session identity changed")

    @asynccontextmanager
    async def foreground(self, runtime):
        if self.owner is not None:
            raise PermissionError("Diagnostic foreground already active")
        self.runtime, self.owner = runtime, asyncio.current_task()
        try:
            yield
        finally:
            self.owner = None
            manager = runtime.approvals
            for identity in self.records:
                record = await manager.store.get_approval(identity)
                if record is not None and record.status in {
                    ApprovalStatus.PENDING, ApprovalStatus.APPROVED,
                }:
                    await manager.store.save_approval_if_status(replace(
                        record, status=ApprovalStatus.CONSUMED,
                        reason="Local diagnostic Session ended; grant revoked"), record.status)
            self.records.clear()
            self.principal, self.session_id = None, ""


class DiagnosticApprovals(ApprovalManager):
    """Uses existing records; no diagnostic request is left waiting for a human."""
    def __init__(self, *args, diagnostics, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostics = diagnostics

    async def decide(self, approval_id, approved, **kwargs):
        if approval_id in self.diagnostics.records:
            kwargs["reason"] = "LOCAL DIAGNOSTIC (not human): " + (kwargs.get("reason") or "")
        record = await super().decide(approval_id, approved, **kwargs)
        if approval_id in self.diagnostics.records and record.status is ApprovalStatus.APPROVED:
            report(self.diagnostics.runtime, "Approval", f"AUTO-APPROVED: {record.tool}")
        return record

    async def request(self, tool, arguments, **kwargs):
        mode = self.diagnostics
        await mode.require()
        task = await mode.runtime.get_task(kwargs.get("task_id", ""))
        if (task is None or task.actor != mode.principal or task.source != "cli"
                or kwargs.get("source") != "cli"
                or kwargs.get("requested_by") != mode.principal.user_id
                or task.metadata.get("session_id") != mode.session_id):
            raise PermissionError("Diagnostic task identity mismatch")
        if tool == "directory.list":
            # Existing analysis_consent.bind revalidates Scope and Policy and
            # immediately decides this exact record, without a pending event.
            from Sprout.task.models import TaskExecutionMode
            if (task.execution_mode is not TaskExecutionMode.READ_ONLY
                    or kwargs.get("session_id") != mode.session_id):
                raise PermissionError("Diagnostic read-only task required")
            approve = False
        elif tool == CODING_TASK_CAPABILITY and _CODING_REQUEST.get() == (self, task.id):
            approve = True
        elif tool in {"process_run", "file.apply"} and _EXACT_REQUEST.get() == (
            self, task.id, tool, self.fingerprint(arguments), kwargs.get("resource_scope")
        ) and kwargs.get("session_id") == mode.session_id:
            # Frozen process permits consume the approved record on their second
            # pass. Exact Apply creates and decides its own plan record.
            approve = tool == "process_run"
        else:
            raise PermissionError("Diagnostic mode refuses this effect; human approval disabled")
        kwargs["request_approval"] = False
        kwargs["ttl_seconds"] = 60
        record = await super().request(tool, arguments, **kwargs)
        mode.records.add(record.id)
        if approve:
            await self.decide(record.id, True, principal=mode.principal,
                              decided_by=mode.principal.user_id, channel="cli",
                              reason="Foreground diagnostic operation")
            record = await self.store.get_approval(record.id)
        return record


@asynccontextmanager
async def foreground(runtime):
    mode = getattr(runtime, "_local_cli_diagnostics", None)
    if mode is None:
        yield
    else:
        async with mode.foreground(runtime):
            yield


def current(runtime):
    mode = getattr(runtime, "_local_cli_diagnostics", None)
    return mode if type(mode) is LocalCLIDiagnostics and mode.active() else None


@contextmanager
def coding_approval(manager, tool, scope):
    """Only the existing, Host-constructed plan-bound Docker route may opt in."""
    if not isinstance(manager, DiagnosticApprovals):
        yield
        return
    from Sprout.execution.coding_command_tool import IsolatedCodingCommandTool
    from Sprout.execution.coding_commands import PlanBoundCodingCommands

    mode = manager.diagnostics
    from Sprout.runtime.filesystem_experiment import FilesystemExperiment
    from Sprout.runtime.isolated_test_run import FilesystemTestRun

    filesystem = (scope is not None and type(tool) is IsolatedCodingCommandTool
                  and type(tool._handler) in {FilesystemExperiment, FilesystemTestRun}
                  and mode.runtime._sandbox_lifecycle.filesystem.get(scope.task.id)
                  is tool._handler and bool(tool._handler.image))
    if (not mode.active() or scope is None or scope.runtime is not mode.runtime
            or scope.task.actor != mode.principal or scope.session_id != mode.session_id
            or type(tool) is not IsolatedCodingCommandTool
            or (not filesystem and (
                not getattr(scope.runtime, "_isolated_coding_required", False)
                or type(tool._handler) is not PlanBoundCodingCommands
                or tool._handler._metadata is not scope.runtime.storage.metadata))):
        raise PermissionError("Diagnostic isolated coding binding unavailable")
    token = _CODING_REQUEST.set((manager, scope.task.id))
    try:
        yield
    finally:
        _CODING_REQUEST.reset(token)


@contextmanager
def exact_approval(manager, task_id, tool, arguments, resource_scope):
    """Bind an already-validated Broker request to this foreground call."""
    if not isinstance(manager, DiagnosticApprovals):
        yield
        return
    token = _EXACT_REQUEST.set((
        manager, task_id, tool, manager.fingerprint(arguments), resource_scope
    ))
    try:
        yield
    finally:
        _EXACT_REQUEST.reset(token)


def report(runtime, stage, detail):
    mode = current(runtime)
    if mode is not None and mode.progress is not None:
        mode.progress(f"[{stage}] {detail}")
        return
    progress = foreground_progress.get()
    if runtime is not None and progress is not None and progress[:2] == (
            runtime, asyncio.current_task()):
        progress[2](f"[{stage}] {detail}")


def scoped_file_tool(tool):
    from Sprout.execution.sandbox_tool import SandboxEditTool, SandboxReadTool, SandboxWriteTool

    return type(tool) in {SandboxReadTool, SandboxWriteTool, SandboxEditTool}


async def node_authorized(manager, scope, subject):
    """Check the live Host-issued node against the current CLI session."""
    from Sprout.tools.context_keys import _current_project_node

    if not isinstance(manager, DiagnosticApprovals) or scope is None or subject is None:
        return False
    mode = manager.diagnostics
    if (not mode.active() or _current_project_node() is not scope
            or scope.runtime is not mode.runtime or scope.session_id != mode.session_id
            or scope.task.actor != mode.principal or subject.actor != mode.principal
            or subject.task_id != scope.task.id or scope.task.workspace_id != scope.workspace.id):
        return False
    await mode.require()
    owned = await mode.runtime._sessions.require_owner(mode.session_id, mode.principal)
    return mode.runtime.session_workspace_id(owned) == scope.workspace.id


async def foreground_authorized(manager, turn, session_id, requested_by, source):
    if not isinstance(manager, DiagnosticApprovals) or turn is None or not turn.active:
        return False
    mode = manager.diagnostics
    if (not mode.active() or mode.principal is None or source not in {"cli", "interactive"}
            or session_id != mode.session_id or turn.session_id != session_id
            or requested_by != mode.principal.user_id or turn.user_id != requested_by
            or mode.runtime._capability_turns.get(session_id) is not turn):
        return False
    await mode.require()
    owned = await mode.runtime._sessions.require_owner(session_id, mode.principal)
    workspace_id = mode.runtime.session_workspace_id(owned)
    workspace = await mode.runtime.get_workspace(workspace_id) if workspace_id else None
    return (workspace is not None and workspace.id == turn.workspace_id
            and workspace.root.is_dir() and workspace.root.resolve() == turn.root)


async def tool_refusal(manager, tool, scope):
    if not isinstance(manager, DiagnosticApprovals):
        return ""
    if not manager.diagnostics.active():
        return "Diagnostic mode refuses background tool execution"
    from Sprout.execution.coding_command_tool import IsolatedCodingCommandTool
    from Sprout.tools.project_analyze_tool import ProjectAnalyzeTool
    from Sprout.tools.registry import RequestWorkspaceTool
    from Sprout.tools.workspace_query_tool import WorkspaceQueryTool

    if type(tool) in {ProjectAnalyzeTool, WorkspaceQueryTool, RequestWorkspaceTool}:
        return ""  # Existing scoped analysis admission remains mandatory.
    if type(tool) is IsolatedCodingCommandTool and scope is not None:
        return ""  # coding_approval and the real Docker backend still validate.
    return "Diagnostic mode refuses effects without a bound workspace or isolated executor"


def process_refused(manager, task_id, command, *, frozen_scope=None):
    if not isinstance(manager, DiagnosticApprovals):
        return False
    from Sprout.execution.coding_backend import _CODING_PROCESS

    mode = manager.diagnostics
    if not mode.active():
        return True
    if frozen_scope is not None:
        return not (frozen_scope.runtime is mode.runtime
                    and frozen_scope.task.id == task_id
                    and frozen_scope.task.actor == mode.principal
                    and frozen_scope.session_id == mode.session_id)
    coding = _CODING_PROCESS.get()
    return not (coding is not None
                and coding[:3] == (asyncio.current_task(), task_id, tuple(command))
                and coding[3] is not None)
