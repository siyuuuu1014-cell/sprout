"""Real B1/Metadata/FileBroker gates with a controlled secure-open test double.

Positive cases are NOT native Windows or POSIX secure-open evidence.
"""

import asyncio
from copy import copy
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.execution import file_broker
from Sprout.execution.file_snapshot import read_fd
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.security.engine import PolicyEngine
from Sprout.task.models import DelegationScope
from Sprout.tests.test_r04_b21_snapshot import no_approval_activity as no_approval_activity
from Sprout.tests.test_r04_b21_snapshot import (
    unchanged_approval_storage as unchanged_approval_storage,
)
from Sprout.tests.test_r04_pa2_b1_binding import issued, node_scope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.tools import context_keys as keys
from Sprout.workspace.authorized_content import ContentLimits, _CaptureSelection


@pytest.fixture
def captured(rig, monkeypatch):
    sources = {
        "src/main.py": b"from src.worker import work\ndef main():\n    return work()\n",
        "src/worker.py": b"def work():\n    return 1\n",
        "src/private/denied.py": b"private_data = 1\n",
        "src2/outside.py": b"outside = 1\n",
        "src/.env": b"TOKEN=not-visible\n",
        "src/credentials.json": b'{"key":"not-visible"}',
        "src/data.db": b"not-visible",
        "src/narrow/only.py": b"def narrow():\n    return 1\n",
    }
    for path, data in sources.items():
        target = rig.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    opens = []
    def boundary(root, target, operation, authorize, **kwargs):
        authorize()
        opens.append(target.relative_to(rig.root).as_posix())
        with target.open("rb") as stream:
            return read_fd(stream.fileno(), kwargs["content"])
    monkeypatch.setattr(file_broker, "file_operation", boundary)
    spy = AsyncMock(wraps=rig.runtime._workspaces.analyze_view)
    monkeypatch.setattr(rig.runtime._workspaces, "analyze_view", spy)
    return SimpleNamespace(opens=opens, spy=spy)


async def test_real_binding_broker_view_intelligence_without_old_callbacks(rig, captured):
    with node_scope(rig):
        tool = await issued(rig)
        tool._analyze = AsyncMock(side_effect=AssertionError("legacy callback"))
        tool.invoke = AsyncMock(side_effect=AssertionError("tool capability restored"))
        with keys._project_analysis_call(tool):
            selection = await rig.runtime._issue_project_analysis_selection(
                ("src/main.py", "src/worker.py"))
            result = await rig.runtime._analyze_project_selection(selection)
            with pytest.raises(PermissionError):
                await rig.runtime._analyze_project_selection(selection)
        tool.invoke.assert_not_called()
        tool._analyze.assert_not_called()
    assert captured.opens == ["src/main.py", "src/worker.py"]
    assert set(result.fingerprint.resource_hashes) == set(captured.opens)
    assert any(edge.relation == "imports" for edge in result.graph.edges)
    view = captured.spy.call_args.args[2]
    with pytest.raises(PermissionError):
        view.check()


async def test_existing_broad_snapshot_cache_is_not_read_or_updated(rig, captured, monkeypatch):
    from Sprout.workspace.authorized_content import AuthorizedContentView, AuthorizedEntry
    from Sprout.workspace.intelligence import WorkspaceIntelligence

    # Synthetic fixture contents are NOT authorized against the Task's Scope.
    # They seed an existing broad cache solely to verify non-use by B2.
    broad_view = AuthorizedContentView(rig.workspace.id, (
        AuthorizedEntry.synthetic("src2/outside.py", b"def hidden():\n    return 1\n"),
    ))
    broad = await WorkspaceIntelligence().analyze_view(rig.workspace, rig.task, broad_view)
    cache = rig.runtime._workspaces._analysis_cache
    cache.put(rig.workspace.id, broad)
    before = dict(cache._entries)
    put = Mock(side_effect=AssertionError("restricted cache write"))
    # Slots require patching the class; the existing get/persist guards remain.
    monkeypatch.setattr(type(cache), "put", put)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
            result = await rig.runtime._analyze_project_selection(selected)
    assert set(result.fingerprint.resource_hashes) == {"src/main.py"}
    assert not any("hidden" in node.name for node in result.graph.nodes)
    assert cache._entries == before
    put.assert_not_called()


@pytest.mark.parametrize("paths", [
    ("src/private/denied.py",), ("src/main.py", "src/private/denied.py"),
    ("src2/outside.py",), ("../parent.py",), ("/absolute.py",),
    ("src/.env",), ("src/credentials.json",), ("src/data.db",),
])
async def test_denied_selection_never_publishes(rig, captured, paths):
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            with pytest.raises((PermissionError, ValueError)):
                selection = await rig.runtime._issue_project_analysis_selection(paths)
                await rig.runtime._analyze_project_selection(selection)
    captured.spy.assert_not_called()
    assert not any(path.startswith("src/private") or path.startswith("src2/")
                   or path in {"src/.env", "src/credentials.json", "src/data.db"}
                   for path in captured.opens)


@pytest.mark.parametrize("action", [ActionType.FILE_READ, ActionType.TOOL_INVOKE])
@pytest.mark.parametrize("decision", [AccessDecision.DENY, AccessDecision.REQUIRE_APPROVAL])
async def test_policy_intersection_no_approval(rig, captured, monkeypatch, action, decision):
    class Policy(PolicyEngine):
        def decide(self, request):
            if request.action is action:
                return PolicyDecision(decision, "server denial")
            return super().decide(request)
    policy = Policy()
    monkeypatch.setattr(rig.runtime, "_policy_engine", policy)
    monkeypatch.setattr(rig.runtime._file_broker, "_policy", policy)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            with pytest.raises(PermissionError):
                selection = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
                await rig.runtime._analyze_project_selection(selection)
    assert captured.opens == []
    captured.spy.assert_not_called()


async def test_each_selected_resource_preserves_path_specific_tool_deny(rig, captured, monkeypatch):
    class Policy(PolicyEngine):
        def decide(self, request):
            if (request.action is ActionType.TOOL_INVOKE
                    and request.resource.path.endswith("/worker.py")):
                return PolicyDecision(AccessDecision.DENY, "per-resource tool denial")
            return super().decide(request)
    policy = Policy()
    monkeypatch.setattr(rig.runtime, "_policy_engine", policy)
    monkeypatch.setattr(rig.runtime._file_broker, "_policy", policy)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await rig.runtime._issue_project_analysis_selection(
                ("src/main.py", "src/worker.py"))
            with pytest.raises(PermissionError):
                await rig.runtime._analyze_project_selection(selected)
    assert "src/worker.py" not in captured.opens
    captured.spy.assert_not_called()


async def test_raw_json_copy_and_other_runtime_are_not_selectors(rig, captured):
    from Sprout.tests.conftest import build_runtime

    other, _ = build_runtime(storage=rig.storage)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
            for value in ({"paths": ["src/main.py"], "trusted": True}, copy(selected),
                          _CaptureSelection(("src/main.py",), ContentLimits())):
                with pytest.raises(PermissionError):
                    await rig.runtime._analyze_project_selection(value)
            with pytest.raises(PermissionError):
                await other._analyze_project_selection(selected)
    with pytest.raises(PermissionError):
        await rig.runtime._analyze_project_selection(selected)
    assert captured.opens == []


async def test_copied_call_context_and_other_frame_do_not_authorize(rig, captured):
    with node_scope(rig):
        tool, other = await issued(rig), await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
            with pytest.raises(PermissionError):
                await asyncio.create_task(rig.runtime._analyze_project_selection(selected))
            with keys._project_analysis_call(other):
                with pytest.raises(PermissionError):
                    await rig.runtime._analyze_project_selection(selected)
    assert captured.opens == []


@pytest.mark.parametrize("actions", [{"tool.invoke"}, {"file.read"}])
async def test_independent_tool_and_file_actions(rig, captured, actions):
    rig.task = replace(rig.task, delegation_scope=DelegationScope(
        allowed_actions=frozenset(actions), allowed_paths=(str(rig.root / "src"),)))
    await rig.storage.metadata.save_task(rig.task)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            with pytest.raises(PermissionError):
                selected = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
                await rig.runtime._analyze_project_selection(selected)
    assert captured.opens == []


async def test_runtime_security_policy_deny_preserved(rig, captured, monkeypatch):
    from Sprout.security.policy import SecurityPolicy

    monkeypatch.setattr(rig.runtime, "security", SimpleNamespace(
        tool_policy=SecurityPolicy(always_deny=frozenset({"project_analyze"})), audit=Mock()))
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            with pytest.raises(PermissionError):
                await rig.runtime._issue_project_analysis_selection(("src/main.py",))
    assert captured.opens == []


async def test_scope_changed_since_selection_denies_before_open(rig, captured):
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
            await rig.storage.metadata.save_task(replace(
                rig.task, delegation_scope=DelegationScope(denied_paths=("",))))
            with pytest.raises(PermissionError):
                await rig.runtime._analyze_project_selection(selected)
    assert captured.opens == []


@pytest.mark.parametrize("limits", [
    {"max_files": 1}, {"max_depth": 1}, {"max_file_bytes": 1}, {"max_total_bytes": 1},
])
async def test_capture_budget_failure_has_no_partial_result(rig, captured, limits):
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            with pytest.raises((PermissionError, ValueError)):
                selection = await rig.runtime._issue_project_analysis_selection(
                    ("src/main.py", "src/worker.py"), limits=ContentLimits(**limits))
                await rig.runtime._analyze_project_selection(selection)
    captured.spy.assert_not_called()


async def test_revoke_after_analysis_prevents_publication(rig, captured, monkeypatch):
    original = rig.runtime._workspaces.analyze_view
    async def analyze(*args):
        result = await original(*args)
        await rig.storage.metadata.save_task(replace(
            rig.task, delegation_scope=DelegationScope(denied_paths=("",))))
        return result
    monkeypatch.setattr(rig.runtime._workspaces, "analyze_view", analyze)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selection = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
            with pytest.raises(PermissionError):
                await rig.runtime._analyze_project_selection(selection)
    assert captured.opens == ["src/main.py"]


async def test_final_audit_callback_cannot_publish_revoked_view(rig, captured, monkeypatch):
    records = []
    file_events = 0
    async def publish(event):
        nonlocal file_events
        records.append(event)
        if event.payload.get("action") == "file.read":
            file_events += 1
            if file_events == 2:
                await rig.storage.metadata.save_task(replace(
                    rig.task, delegation_scope=DelegationScope(denied_paths=("",))))
    monkeypatch.setattr(rig.runtime, "events", SimpleNamespace(publish=publish))
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
            with pytest.raises(PermissionError):
                await rig.runtime._analyze_project_selection(selected)
    assert file_events == 2
    assert all(event.payload["task_id"] == rig.task.id for event in records)


async def test_native_failure_no_path_fallback(rig, captured, monkeypatch):
    from Sprout.execution.file_access import FileBoundaryError

    backend = Mock(side_effect=FileBoundaryError("local_volume_identity_unproved"))
    monkeypatch.setattr(file_broker, "file_operation", backend)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
            with pytest.raises(PermissionError):
                await rig.runtime._analyze_project_selection(selected)
    backend.assert_called_once()
    captured.spy.assert_not_called()


async def test_same_session_concurrent_tasks_do_not_union_scopes(rig, captured):
    from Sprout.orchestration.models import ExecutionNode, NodeType

    second_task = replace(rig.task, id="narrow-task", delegation_scope=DelegationScope(
        allowed_actions=frozenset({"tool.invoke", "file.read"}),
        allowed_paths=(str(rig.root / "src/narrow"),)))
    await rig.storage.metadata.save_task(second_task)
    pending = ExecutionNode(id="narrow-node", task_id=second_task.id, type=NodeType.AGENT)
    await rig.storage.metadata.save_execution_node(pending)
    assert await rig.commission(pending, "narrow-owner", "")
    second_node = await rig.storage.metadata.get_execution_node(pending.id)
    other = SimpleNamespace(**{**vars(rig), "task": second_task, "node": second_node})
    async def call(subject, paths):
        with node_scope(subject):
            tool = await issued(subject)
            with keys._project_analysis_call(tool):
                selected = await subject.runtime._issue_project_analysis_selection(paths)
                return await subject.runtime._analyze_project_selection(selected)
    first, second = await asyncio.gather(call(rig, ("src/main.py",)),
                                         call(other, ("src/narrow/only.py",)))
    assert set(first.fingerprint.resource_hashes) == {"src/main.py"}
    assert set(second.fingerprint.resource_hashes) == {"src/narrow/only.py"}


async def test_cancellation_joins_capture_and_never_analyzes(rig, captured, monkeypatch):
    from Sprout.execution.file_access import FileBoundaryError

    entered, ended = Event(), Event()
    def blocked(root, target, operation, authorize, **kwargs):
        authorize()
        entered.set()
        try:
            assert kwargs["content"].cancelled.wait(5)
            raise FileBoundaryError("snapshot_cancelled")
        finally:
            ended.set()
    monkeypatch.setattr(file_broker, "file_operation", blocked)
    async def call():
        with node_scope(rig):
            tool = await issued(rig)
            with keys._project_analysis_call(tool):
                selected = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
                return await rig.runtime._analyze_project_selection(selected)
    task = asyncio.create_task(call())
    while not entered.is_set() and not task.done():
        await asyncio.sleep(0.001)
    assert entered.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ended.is_set()
    captured.spy.assert_not_called()
