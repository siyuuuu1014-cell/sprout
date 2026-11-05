"""B1: Runtime-issued node subjects propagate identity, never enable reads."""

import asyncio
from contextvars import copy_context
from copy import copy, deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.agent.executor import ActionExecutor
from Sprout.events import TOOL_DENIED
from Sprout.gateway.identity import Principal
from Sprout.llm.messages import ToolCall
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.security.policy import SecurityPolicy
from Sprout.session.models import Session
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import DelegationScope, Task, TaskStatus
from Sprout.tests.conftest import build_runtime
from Sprout.tools import context_keys as keys
from Sprout.tools import project_analyze_tool as module
from Sprout.tools.context_keys import NodeToolInvocationContext

SCOPE = "project_analysis_scope_unavailable"
BINDING = "project_analysis_binding_denied"


@pytest.fixture(autouse=True)
def no_effects(monkeypatch):
    effect = AsyncMock(side_effect=AssertionError("Analysis/process/Temporal reached"))
    path = Mock(side_effect=AssertionError("Project file access reached"))
    monkeypatch.setattr(module, "Path", path)
    monkeypatch.setattr(module, "_system_tool_probe", effect)
    monkeypatch.setattr(module, "_run", effect)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", effect)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", effect)
    monkeypatch.setattr("Sprout.orchestration.terminal.ensure.ensure_temporal_async", effect)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    yield
    path.assert_not_called()
    effect.assert_not_called()


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    storage = StorageBundle.in_memory()
    storage.metadata = open_metadata_store(":memory:")
    runtime, executor = build_runtime(storage=storage)
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = await runtime.open_workspace(str(root))
    principal = Principal(user_id="owner", source="cli", authenticated=True)
    session = Session(id="real-session", user_id="owner", metadata={
        "entry_principal_v1": {"user_id": "owner", "source": "cli", "authenticated": True}})
    await runtime._session_store.save_session(session)
    task = Task(id="b1-task", workspace_id=workspace.id, actor=principal, source="cli",
                status=TaskStatus.RUNNING, metadata={"session_id": session.id},
                delegation_scope=DelegationScope(
                    allowed_actions=frozenset({"tool.invoke", "file.read"}),
                    allowed_paths=(str(root / "src"),),
                    denied_paths=(str(root / "src" / "private"),)))
    await storage.metadata.save_task(task)
    pending = ExecutionNode(id="b1-node", task_id=task.id, type=NodeType.AGENT)
    await storage.metadata.save_execution_node(pending)
    assert await storage.metadata.claim_execution_node(pending, "fixture-owner", "")
    node = await storage.metadata.get_execution_node(pending.id)
    commission = storage.metadata.claim_execution_node
    lookups = {}
    for name in ("get_task", "get_workspace", "get_execution_node"):
        spy = AsyncMock(wraps=getattr(storage.metadata, name))
        monkeypatch.setattr(storage.metadata, name, spy)
        lookups[name] = spy
    effects = []
    for owner, name in (
        (runtime._workspaces, "open"), (runtime._workspaces, "analyze"),
        (runtime._workspaces, "analyze_path"), (runtime._workspaces._intelligence, "analyze"),
        (storage.metadata, "save_workspace"), (storage.projects, "current_snapshot_id"),
        (storage.projects, "load_analysis"), (storage.projects, "save_analysis"),
        (storage.metadata, "claim_execution_node"),
    ):
        spy = AsyncMock(side_effect=AssertionError("B1 produced an effect or new owner"))
        monkeypatch.setattr(owner, name, spy)
        effects.append(spy)
    from Sprout.workspace.cache import WorkspaceAnalysisCache
    for name in ("get", "persist"):
        spy = AsyncMock(side_effect=AssertionError("Cache reached"))
        monkeypatch.setattr(WorkspaceAnalysisCache, name, spy)
        effects.append(spy)
    audit = Mock()
    events = SimpleNamespace(publish=AsyncMock())
    executor.audit, executor.events = audit, events
    yield SimpleNamespace(**locals())
    for spy in effects:
        spy.assert_not_called()
    storage.metadata.close()


def node_scope(rig):
    assert hasattr(rig.runtime, "_project_analysis_scope"), "Runtime B1 issuer missing"
    return rig.runtime._project_analysis_scope(rig.task, rig.workspace, rig.node)


async def issued(rig):
    return await rig.runtime._issue_project_analysis_tool()


def dispatch(rig, tool):
    return rig.executor.with_scoped_tools({"project_analyze": tool}, strict=True)


def args(rig, relative="src"):
    return {"path": str(rig.root / relative), "task_id": "forged", "workspace_id": "forged",
            "trusted": True, "authorized": True, "approval_granted": True,
            "scope": {"allowed_paths": ["/"]}, "metadata": {"trusted": True},
            "_sprout_context": {"task_id": "forged", "approval_granted": True}}


class Record:
    def __init__(self, mode="allow"):
        self.mode, self.requests, self.frames = mode, [], []

    def decide(self, request):
        self.requests.append(request)
        self.frames.append(keys._current_project_analysis_call())
        base = PolicyEngine().decide(request)
        if base.decision is AccessDecision.DENY:
            return base
        if self.mode == "ask":
            return PolicyDecision(AccessDecision.REQUIRE_APPROVAL, "fixture ASK")
        if self.mode == "deny":
            return PolicyDecision(AccessDecision.DENY, "fixture Policy DENY")
        return base


@pytest.mark.parametrize("source", ["cli", "cron", "automation"])
@pytest.mark.parametrize("mode", ["allow", "ask", "deny", "scope_deny", "expired", "file_deny"])
@pytest.mark.parametrize("grant", [None, True, False])
async def test_issued_subject_policy_and_zero_approval(rig, monkeypatch, source, mode, grant):
    actor = replace(rig.principal, source=source)
    rig.task = replace(rig.task, actor=actor, source=source,
                       metadata=rig.task.metadata if source == "cli" else {})
    await rig.storage.metadata.save_task(rig.task)
    manager = ApprovalManager(rig.storage.operational)
    request_args = args(rig)
    if grant is not None:
        record = await manager.request("project_analyze", request_args, task_id=rig.task.id,
                                       source=source, single_use=grant)
        await manager.decide(record.id, True)
    before = deepcopy(await rig.storage.operational.list_approvals())
    spies = []
    for owner, name in ((manager, "request"), (manager, "is_approved"),
                        (rig.storage.operational, "save_approval"),
                        (rig.storage.operational, "save_approval_if_status")):
        spy = AsyncMock(wraps=getattr(owner, name))
        monkeypatch.setattr(owner, name, spy)
        spies.append(spy)
    for name in ("mode_for", "asks_a_human"):
        spy = Mock(wraps=getattr(manager, name))
        monkeypatch.setattr(manager, name, spy)
        spies.append(spy)
    policy = Record(mode)
    with node_scope(rig):
        tool = await issued(rig)
        if mode in {"scope_deny", "expired", "file_deny"}:
            scope = rig.task.delegation_scope
            if mode == "expired":
                scope = replace(scope, expires_at=datetime.now(UTC) - timedelta(seconds=1))
            else:
                scope = replace(scope, denied_actions=frozenset({
                    "tool.invoke" if mode == "scope_deny" else "file.read"}))
            await rig.storage.metadata.save_task(replace(rig.task, delegation_scope=scope))
        for spy in rig.lookups.values():
            spy.reset_mock()
        executor = dispatch(rig, tool)
        executor.approvals, executor.policy_engine = manager, policy
        result = await ActionExecutor(executor).run_call(
            ToolCall(id="model", name="project_analyze", arguments=request_args),
            requested_by="owner", session_id=f"node-{rig.node.id}")
    assert not result.ok and result.approval_id is None
    denied = mode in {"deny", "scope_deny", "expired"}
    assert result.error == ("Tool project_analyze is denied by the security policy"
                            if denied else SCOPE)
    for name, value in (("get_task", rig.task.id), ("get_workspace", rig.workspace.id),
                        ("get_execution_node", rig.node.id)):
        rig.lookups[name].assert_awaited_with(value)
    request = policy.requests[0]
    assert request.task_id == rig.task.id and request.actor == actor
    assert request.resource.workspace_id == rig.workspace.id
    assert request.resource.path.replace("\\", "/").endswith("/workspace/src")
    assert request.scope.allowed_paths and not request.scope.permits(
        "file.read", str(rig.root / "src" / "private" / "file.py"))
    if mode == "file_deny":
        assert not request.scope.permits("file.read", request.resource.path)
    assert all(frame is not None and not frame.active for frame in policy.frames)
    assert keys._current_project_analysis_call() is None
    assert await rig.storage.operational.list_approvals() == before
    for spy in spies:
        spy.assert_not_called()
    assert rig.audit.record.call_args.args[1]["task_id"] == rig.task.id
    assert rig.events.publish.call_args.args[0].name == TOOL_DENIED
    if mode == "deny":
        assert rig.audit.record.call_args.args[1]["reason"] == "fixture Policy DENY"
    if mode == "expired":
        assert "expired" in rig.audit.record.call_args.args[1]["reason"]


@pytest.mark.parametrize("bad", [
    "missing_task", "missing_workspace", "workspace_mismatch", "root_changed",
    "completed", "cancelled", "unauthenticated", "actor_changed", "session_owner",
    "session_source", "session_changed", "node_owner", "node_task", "node_finished",
])
async def test_authority_changes_after_issue_are_rejected(rig, monkeypatch, bad):
    with node_scope(rig):
        tool = await issued(rig)
        task, node = rig.task, rig.node
        if bad == "missing_task":
            monkeypatch.setattr(rig.storage.metadata, "get_task", AsyncMock(return_value=None))
        elif bad == "missing_workspace":
            monkeypatch.setattr(rig.storage.metadata, "get_workspace", AsyncMock(return_value=None))
        elif bad == "workspace_mismatch":
            await rig.storage.metadata.save_task(replace(task, workspace_id="other"))
        elif bad == "root_changed":
            monkeypatch.setattr(rig.storage.metadata, "get_workspace", AsyncMock(
                return_value=replace(rig.workspace, root=rig.root.parent / "changed")))
        elif bad in {"completed", "cancelled"}:
            await rig.storage.metadata.save_task(replace(
                task, status=TaskStatus.COMPLETED if bad == "completed" else TaskStatus.CANCELLED))
        elif bad in {"unauthenticated", "actor_changed"}:
            await rig.storage.metadata.save_task(replace(task, actor=replace(
                task.actor, authenticated=False) if bad == "unauthenticated"
                else replace(task.actor, user_id="other")))
        elif bad.startswith("session_"):
            if bad == "session_changed":
                await rig.storage.metadata.save_task(replace(
                    task, metadata={"session_id": "other"}))
            else:
                session = replace(rig.session, user_id="other") if bad == "session_owner" else (
                    replace(rig.session, metadata={"entry_principal_v1": {
                        "user_id": "owner", "source": "web", "authenticated": True}}))
                await rig.runtime._session_store.save_session(session)
        elif bad == "node_owner":
            monkeypatch.setattr(rig.storage.metadata, "get_execution_node", AsyncMock(
                return_value=replace(node, metadata={
                    "execution_guard": {"owner": "other", "state": "active"}})))
        elif bad == "node_task":
            monkeypatch.setattr(rig.storage.metadata, "get_execution_node", AsyncMock(
                return_value=replace(node, task_id="other")))
        else:
            assert await rig.storage.metadata.finish_execution_node(replace(
                node, status=NodeStatus.COMPLETED), "fixture-owner", "completed")
        result = await dispatch(rig, tool).execute("project_analyze", args(rig))
        assert result.error == BINDING


async def test_cross_runtime_copy_unissued_and_direct_remain_closed(rig):
    other, other_executor = build_runtime(storage=rig.storage)
    with node_scope(rig):
        tool = await issued(rig)
        result = await tool.invoke(args(rig))
        assert result.error == SCOPE
        for candidate in (copy(tool), module.ProjectAnalyzeTool()):
            assert (await dispatch(rig, candidate).execute(
                "project_analyze", args(rig))).error == BINDING
        with other._project_analysis_scope(rig.task, rig.workspace, rig.node):
            executor = other_executor.with_scoped_tools({"project_analyze": tool}, strict=True)
            assert (await executor.execute("project_analyze", args(rig))).error == BINDING
    assert (await dispatch(rig, tool).execute("project_analyze", args(rig))).error == SCOPE


async def test_call_frame_copy_child_and_invalidation(rig):
    captured = []
    with node_scope(rig):
        tool = await issued(rig)
        executor = dispatch(rig, tool)
        policy = Record()
        executor.policy_engine = policy

        # Child dispatch must not recurse through this event publisher.
        child_executor = dispatch(rig, tool)
        async def publish_once(event):
            captured.append(copy_context())
            async def child():
                assert keys._current_project_analysis_call() is None
                result = await child_executor.execute("project_analyze", args(rig))
                assert result.error == SCOPE
            await asyncio.create_task(child())
        executor.events = SimpleNamespace(publish=AsyncMock(side_effect=publish_once))
        assert (await executor.execute("project_analyze", args(rig))).error == SCOPE
    assert captured[0].run(keys._current_project_analysis_call) is None
    assert all(not frame.active for frame in policy.frames)


@pytest.mark.parametrize("interrupt", ["cancel", "failure"])
async def test_lookup_interruption_invalidates_frame(rig, monkeypatch, interrupt):
    frames = []
    with node_scope(rig):
        tool = await issued(rig)
        async def interrupted(task_id):
            frames.append(keys._current_project_analysis_call())
            if interrupt == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("metadata unavailable")
        monkeypatch.setattr(rig.storage.metadata, "get_task", interrupted)
        if interrupt == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await dispatch(rig, tool).execute("project_analyze", args(rig))
        else:
            result = await dispatch(rig, tool).execute("project_analyze", args(rig))
            assert result.error == BINDING
    assert frames and all(frame is not None and not frame.active for frame in frames)
    assert keys._current_project_analysis_call() is None


async def test_same_session_concurrent_tasks_and_callback_mutation(rig):
    second = replace(rig.task, id="b1-second", delegation_scope=replace(
        rig.task.delegation_scope, allowed_paths=(str(rig.root / "lib"),)))
    pending = ExecutionNode(id="b1-second-node", task_id=second.id, type=NodeType.PLAN)
    await rig.storage.metadata.save_task(second)
    await rig.storage.metadata.save_execution_node(pending)
    # Initial fixture commissioning only, before the zero-reclaim assertion.
    assert await rig.commission(pending, "second-owner", "")
    node = await rig.storage.metadata.get_execution_node(pending.id)
    results = []
    async def run(task, running, relative):
        with rig.runtime._project_analysis_scope(task, rig.workspace, running):
            tool = await issued(rig)
            tool._analyze = AsyncMock(side_effect=AssertionError("Mutated callback ran"))
            executor, policy = dispatch(rig, tool), Record()
            executor.policy_engine = policy
            payload = args(rig, relative)
            result = await executor.execute("project_analyze", payload)
            tool._analyze.assert_not_called()
            results.append((result, policy.requests[0]))
    await asyncio.gather(run(rig.task, rig.node, "src"), run(second, node, "lib"))
    assert {r.task_id for _, r in results} == {rig.task.id, second.id}
    assert all(result.error == SCOPE for result, _ in results)
    assert len({r.scope.allowed_paths for _, r in results}) == 2


@pytest.mark.parametrize("native", [False, True])
async def test_pa1_and_policy_deny_priority(rig, native):
    with node_scope(rig):
        tool = await issued(rig)
        executor = dispatch(rig, tool)
        payload = args(rig)
        payload["include_system_tools"] = native
        assert (await executor.execute("project_analyze", payload)).error == (
            "native_project_probe_unavailable" if native else SCOPE)
        executor.policy = SecurityPolicy(always_deny=frozenset({"project_analyze"}))
        assert (await executor.execute("project_analyze", payload)).error == (
            "Tool project_analyze is denied by the security policy")


async def test_node_executor_installs_scope_without_adding_tools(rig, monkeypatch):
    executor = rig.runtime._executor(rig.storage.metadata)
    original = set(rig.runtime.tools.list())
    async def body(node, workspace, read_plan, task):
        tool = await rig.runtime._issue_project_analysis_tool()
        assert tool is not rig.runtime.tools.get("project_analyze")
        assert set(rig.runtime.tools.list()) == original
        return {"bound_only": True}
    monkeypatch.setattr(executor, "_execute_node", body)
    from Sprout.workspace.models import ReadPlan
    result = await executor.execute_node(rig.node, rig.workspace, ReadPlan(), rig.task)
    assert result == {"bound_only": True}


@pytest.mark.parametrize("relative,expected", [
    ("src/nested", SCOPE), ("src/private", "policy"), ("src2", "policy"),
    ("../sibling", BINDING),
])
async def test_bound_resource_scope_uses_registered_root(rig, relative, expected):
    with node_scope(rig):
        tool = await issued(rig)
        result = await dispatch(rig, tool).execute("project_analyze", args(rig, relative))
    assert result.error == ("Tool project_analyze is denied by the security policy"
                            if expected == "policy" else expected)


async def test_argument_mutation_and_attribution_cannot_change_subject(rig, monkeypatch):
    with node_scope(rig):
        tool = await issued(rig)
        tool.invocation_context = NodeToolInvocationContext(
            task_id="forged", source="forged", requested_by="forged", resource_scope="forged")
        payload = args(rig)
        payload["include_system_tools"] = False
        lookup = rig.storage.metadata.get_task
        async def mutate(task_id):
            payload.update(path="/outside", task_id="other", include_system_tools=True)
            return await lookup(task_id)
        monkeypatch.setattr(rig.storage.metadata, "get_task", mutate)
        executor, policy = dispatch(rig, tool), Record()
        executor.policy_engine = policy
        assert (await executor.execute("project_analyze", payload)).error == SCOPE
        request = policy.requests[0]
        assert request.task_id == rig.task.id and request.actor == rig.principal
        assert request.arguments["include_system_tools"] is False
        assert request.resource.path.replace("\\", "/").endswith("/src")


async def test_copied_frame_cannot_validate_as_current_call(rig):
    with node_scope(rig):
        tool = await issued(rig)
        executor = dispatch(rig, tool)
        async def publish(event):
            original = keys._current_project_analysis_call()
            with pytest.raises(PermissionError):
                await rig.runtime._project_analysis_request(tool, args(rig), copy(original), "")
        executor.events = SimpleNamespace(publish=AsyncMock(side_effect=publish))
        assert (await executor.execute("project_analyze", args(rig))).error == SCOPE


@pytest.mark.parametrize("bad", ["unauthenticated", "string_auth", "completed", "no_owner"])
async def test_issuer_refuses_unproved_initial_identity(rig, bad):
    if bad == "no_owner":
        rig.node = replace(rig.node, metadata={})
    else:
        task = (replace(rig.task, status=TaskStatus.COMPLETED) if bad == "completed"
                else replace(rig.task, actor=replace(
                    rig.task.actor, authenticated="yes" if bad == "string_auth" else False)))
        rig.task = task
        await rig.storage.metadata.save_task(task)
    with node_scope(rig):
        with pytest.raises(PermissionError):
            await issued(rig)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("policy_deny", [False, True])
async def test_binding_failure_does_not_override_policy_or_pa1(
    rig, monkeypatch, native, policy_deny,
):
    with node_scope(rig):
        tool = await issued(rig)
        monkeypatch.setattr(rig.storage.metadata, "get_task", AsyncMock(return_value=None))
        executor = dispatch(rig, tool)
        policy = Record()
        executor.policy_engine = policy
        if policy_deny:
            executor.policy = SecurityPolicy(always_deny=frozenset({"project_analyze"}))
        payload = args(rig)
        payload["include_system_tools"] = native
        result = await executor.execute("project_analyze", payload)
        expected = ("Tool project_analyze is denied by the security policy" if policy_deny
                    else "native_project_probe_unavailable" if native else BINDING)
        assert result.error == expected
        assert not policy.requests  # No default/unrestricted fallback subject.
        if policy_deny:
            assert rig.audit.record.call_args.args[1]["reason"] == (
                executor.policy.decide_for_spec(rig.runtime.tools.get("project_analyze").spec).reason)


@pytest.mark.parametrize("supplied", ["", "real-session", "node-b1-node", "foreign"])
async def test_real_session_is_checked_not_the_synthetic_node_label(rig, monkeypatch, supplied):
    with node_scope(rig):
        tool = await issued(rig)
        owner = AsyncMock(wraps=rig.runtime._sessions.require_owner)
        monkeypatch.setattr(rig.runtime._sessions, "require_owner", owner)
        result = await dispatch(rig, tool).execute(
            "project_analyze", args(rig), session_id=supplied)
        assert result.error == (BINDING if supplied == "foreign" else SCOPE)
        owner.assert_awaited_once_with("real-session", rig.principal)


async def test_concurrent_same_session_does_not_merge_principals(rig):
    other = replace(rig.task, id="other-owner-task", actor=replace(
        rig.task.actor, user_id="other-owner"))
    pending = ExecutionNode(id="other-owner-node", task_id=other.id, type=NodeType.AGENT)
    await rig.storage.metadata.save_task(other)
    await rig.storage.metadata.save_execution_node(pending)
    assert await rig.commission(pending, "other-owner", "")
    node = await rig.storage.metadata.get_execution_node(pending.id)

    async def run(task, current, permitted):
        with rig.runtime._project_analysis_scope(task, rig.workspace, current):
            if not permitted:
                with pytest.raises(PermissionError):
                    await issued(rig)
                return
            tool = await issued(rig)
            executor, policy = dispatch(rig, tool), Record()
            executor.policy_engine = policy
            assert (await executor.execute("project_analyze", args(rig))).error == SCOPE
            assert policy.requests[0].actor == rig.principal
    await asyncio.gather(run(rig.task, rig.node, True), run(other, node, False))
