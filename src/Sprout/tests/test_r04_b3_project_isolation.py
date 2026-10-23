"""B3 per-call projections. Secure-open positives use B2's controlled backend."""

import asyncio
from copy import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.context.builder import ContextBuilder
from Sprout.message.models import Message
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.security.engine import PolicyEngine
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b21_snapshot import no_approval_activity as no_approval_activity
from Sprout.tests.test_r04_b21_snapshot import (
    unchanged_approval_storage as unchanged_approval_storage,
)
from Sprout.tests.test_r04_pa2_b1_binding import issued, node_scope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.tools import context_keys as keys
from Sprout.tools.workspace_query_tool import WorkspaceQueryTool


@pytest.mark.parametrize("operation", ["symbols", "knowledge", "neighbors", "dependencies"])
async def test_default_query_never_uses_callback_or_old_snapshot(rig, operation):
    callback = AsyncMock(side_effect=AssertionError("broad analysis reached"))
    tool = WorkspaceQueryTool(callback)
    result = await tool.invoke({"workspace_id": rig.workspace.id, "operation": operation,
                                "snapshot_id": "old-wide", "task_id": rig.task.id,
                                "trusted": True, "scope": {"allowed_paths": ["/"]}})
    assert not result.ok
    assert "workspace_query_scope_unavailable" in result.error
    callback.assert_not_called()


async def test_context_legacy_provider_cannot_inject_broad_project(rig):
    callback = AsyncMock(side_effect=AssertionError("broad context analysis reached"))
    builder = ContextBuilder(rig.storage, workspace_analysis_provider=callback)
    message = Message(user_id="owner", content="hidden", metadata={
        "workspace_id": rig.workspace.id, "workspace_context": "hidden broad summary",
        "snapshot_id": "wide", "task_id": rig.task.id, "trusted": True})
    context = await builder.build(message=message, session=rig.session, tools={}, skills={})
    assert context.workspace is None
    assert not context.memory and not context.knowledge
    assert "hidden" not in repr(context.metadata)
    callback.assert_not_called()


@pytest.mark.parametrize("operation", ["symbols", "knowledge", "neighbors", "dependencies"])
async def test_query_projects_only_current_captured_files(rig, captured, operation):
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
            result = await rig.runtime._query_project_selection(selected, {
                "workspace_id": rig.workspace.id, "operation": operation,
                "snapshot_id": "old-wide", "task_id": "forged", "query": ""})
            assert result.ok
            assert "private" not in repr(result.data)
            assert "outside" not in repr(result.data)
            assert "def work" not in repr(result.data)
            with pytest.raises(PermissionError):
                await rig.runtime._query_project_selection(selected, {
                    "workspace_id": rig.workspace.id, "operation": operation})
    assert captured.opens == ["src/main.py"]


async def test_context_is_built_from_view_without_shared_memory(rig, captured, monkeypatch):
    for owner, method in ((rig.runtime._contexts._session_store, "recent_turns"),
                          (rig.runtime._contexts._composer, "assemble")):
        monkeypatch.setattr(owner, method, AsyncMock(side_effect=AssertionError("shared memory")))
    message = Message(user_id="owner", content="main", metadata={
        "workspace_context": "private secret", "workspace_id": "forged", "trusted": True})
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
            context = await rig.runtime._build_project_context(selected, message, rig.session)
            assert context.workspace.workspace_id == rig.workspace.id
            assert "main" in repr(context.workspace)
            assert "private secret" not in repr(context)
            assert not context.memory and not context.knowledge
            assert context.metadata == {}
    assert captured.opens == ["src/main.py"]


@pytest.mark.parametrize("change", ["workspace", "session", "principal", "copy", "raw_analysis"])
async def test_other_subject_or_data_cannot_authorize_query(rig, captured, change):
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await rig.runtime._issue_project_analysis_selection(("src/main.py",))
            with pytest.raises((PermissionError, TypeError)):
                if change == "session":
                    await rig.runtime._build_project_context(
                        selected, Message(user_id="owner", content="main"),
                        replace(rig.session, id="another-session"))
                elif change == "principal":
                    await rig.runtime._build_project_context(
                        selected, Message(user_id="intruder", content="main"), rig.session)
                else:
                    value = copy(selected) if change == "copy" else selected
                    if change == "raw_analysis":
                        value = await rig.runtime._analyze_project_selection(selected)
                    await rig.runtime._query_project_selection(value, {
                        "operation": "symbols", "workspace_id": "other-workspace"
                        if change == "workspace" else rig.workspace.id})


async def test_default_query_executor_does_not_consume_meaningless_approval(rig, monkeypatch):
    from Sprout.tools.spec import ToolSpec

    tool = WorkspaceQueryTool(AsyncMock())
    executor = rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
    tool.spec = replace(tool.spec, risk_level="high")
    assert isinstance(tool.spec, ToolSpec)
    result = await executor.execute(tool.spec.name, {
        "operation": "symbols", "workspace_id": rig.workspace.id}, task_id=rig.task.id)
    assert not result.ok
    assert "workspace_query_scope_unavailable" in (result.error or "")


async def select(rig, path="src/main.py"):
    return await rig.runtime._issue_project_analysis_selection((path,))


async def consume(rig, selected, kind):
    if kind == "query":
        return await rig.runtime._query_project_selection(selected, {
            "workspace_id": rig.workspace.id, "operation": "symbols"})
    return await rig.runtime._build_project_context(
        selected, Message(user_id=rig.principal.user_id, content="main"), rig.session)


@pytest.mark.parametrize("kind", ["query", "context"])
@pytest.mark.parametrize("change", ["scope", "expiry", "task", "workspace", "principal"])
async def test_authority_changes_invalidate_even_hot_cache(rig, captured, kind, change):
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await select(rig)
            task = rig.task
            if change == "scope":
                task = replace(task, delegation_scope=replace(
                    task.delegation_scope, allowed_paths=(str(rig.root / "src/narrow"),)))
            elif change == "expiry":
                task = replace(task, delegation_scope=replace(
                    task.delegation_scope, expires_at=datetime.now(UTC) - timedelta(seconds=1)))
            elif change == "task":
                from Sprout.task.models import TaskStatus
                task = replace(task, status=TaskStatus.COMPLETED)
            elif change == "workspace":
                task = replace(task, workspace_id="other")
            else:
                task = replace(task, actor=replace(task.actor, user_id="another"))
            await rig.storage.metadata.save_task(task)
            with pytest.raises(PermissionError):
                await consume(rig, selected, kind)
    assert captured.opens == []


@pytest.mark.parametrize("kind", ["query", "context"])
async def test_scope_revoked_after_analysis_never_publishes(rig, captured, monkeypatch, kind):
    original = rig.runtime._workspaces.analyze_view
    async def revoke(*args):
        result = await original(*args)
        await rig.storage.metadata.save_task(replace(rig.task, delegation_scope=replace(
            rig.task.delegation_scope, denied_paths=(str(rig.root / "src"),))))
        return result
    monkeypatch.setattr(rig.runtime._workspaces, "analyze_view", revoke)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await select(rig)
            with pytest.raises(PermissionError):
                await consume(rig, selected, kind)


@pytest.mark.parametrize("operation", ["neighbors", "dependencies", "subgraph"])
async def test_query_projection_has_no_host_path_operations(rig, captured, monkeypatch, operation):
    from Sprout.tools.workspace_query_tool import WorkspaceQueryTool
    from Sprout.workspace.query import WorkspaceQuery
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await select(rig)
            analysis = await rig.runtime._analyze_project_selection(selected)
    def forbidden(*args, **kwargs):
        raise AssertionError("projection reopened host path")
    with monkeypatch.context() as patch:
        for method in ("resolve", "stat", "exists", "open", "iterdir", "read_text"):
            patch.setattr(Path, method, forbidden)
        result = WorkspaceQueryTool._project(analysis, {
            "workspace_id": rig.workspace.id, "operation": operation,
            "name": "main", "path": "src/main.py"})
        assert result.ok
        assert WorkspaceQuery(analysis, captured=True)._resource_key("src/main.py")
        rig.runtime._contexts._build_workspace_context(
            analysis, Message(content="main", user_id="owner"))


@pytest.mark.parametrize("kind", ["query", "context"])
async def test_broad_shared_snapshot_never_read_or_written(rig, captured, monkeypatch, kind):
    from Sprout.workspace.authorized_content import AuthorizedContentView, AuthorizedEntry
    from Sprout.workspace.intelligence import WorkspaceIntelligence
    view = AuthorizedContentView(rig.workspace.id, (
        AuthorizedEntry.synthetic("src/private/hidden.py", b"def confidential(): pass\n"),))
    broad = await WorkspaceIntelligence().analyze_view(rig.workspace, rig.task, view)
    cache = rig.runtime._workspaces._analysis_cache
    cache.put(rig.workspace.id, broad)
    before = dict(cache._entries)
    def forbidden(*args, **kwargs):
        raise AssertionError("restricted writeback")
    monkeypatch.setattr(type(cache), "put", forbidden)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            result = await consume(rig, await select(rig), kind)
    assert "confidential" not in repr(result)
    assert "private/hidden" not in repr(result)
    assert cache._entries == before


@pytest.mark.parametrize("kind", ["query", "context"])
@pytest.mark.parametrize("escape", ["child_task", "frame_end", "other_runtime"])
async def test_selection_cannot_escape_call_lifecycle(rig, captured, kind, escape):
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await select(rig)
            if escape == "child_task":
                with pytest.raises(PermissionError):
                    await asyncio.create_task(consume(rig, selected, kind))
            elif escape == "other_runtime":
                from Sprout.tests.conftest import build_runtime
                other, _ = build_runtime(storage=rig.storage)
                with pytest.raises(PermissionError):
                    await other._query_project_selection(selected, {
                        "workspace_id": rig.workspace.id, "operation": "symbols"})
        if escape == "frame_end":
            with pytest.raises(PermissionError):
                await consume(rig, selected, kind)
    assert captured.opens == []


@pytest.mark.parametrize("action", [ActionType.FILE_READ, ActionType.TOOL_INVOKE])
@pytest.mark.parametrize("decision", [AccessDecision.DENY, AccessDecision.REQUIRE_APPROVAL])
async def test_consumer_tool_and_file_policy_are_both_required(
    rig, captured, monkeypatch, action, decision,
):
    class Policy(PolicyEngine):
        def decide(self, request):
            if request.action is action and (
                action is ActionType.FILE_READ
                or request.arguments.get("tool_name") == "workspace_query"
            ):
                return PolicyDecision(decision, "consumer policy")
            return super().decide(request)
    policy = Policy()
    monkeypatch.setattr(rig.runtime, "_policy_engine", policy)
    monkeypatch.setattr(rig.runtime._file_broker, "_policy", policy)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            with pytest.raises(PermissionError):
                await consume(rig, await select(rig), "query")
    assert captured.opens == []


@pytest.mark.parametrize("query", ["private", "hidden", "src2", "confidential", "not-present"])
async def test_hidden_query_terms_do_not_return_resource_metadata(rig, captured, query):
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            result = await rig.runtime._query_project_selection(await select(rig), {
                "workspace_id": rig.workspace.id, "operation": "symbols", "query": query})
    assert result.ok and result.data == []


async def test_shared_argument_mutation_cannot_change_projection(rig, captured, monkeypatch):
    args = {"workspace_id": rig.workspace.id, "operation": "symbols", "query": "main"}
    original = rig.runtime._workspaces.analyze_view
    async def mutate(*values):
        args.update(workspace_id="other", query="private")
        return await original(*values)
    monkeypatch.setattr(rig.runtime._workspaces, "analyze_view", mutate)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            result = await rig.runtime._query_project_selection(await select(rig), args)
    assert result.ok and any(node["qualified_name"] == "main" for node in result.data)


async def test_actual_agent_model_messages_exclude_unpartitioned_memory(rig, captured):
    from Sprout.agent.base import ModelAgent
    from Sprout.llm.messages import LLMResponse
    seen = []
    class Model:
        async def chat(self, messages, **kwargs):
            seen.extend(messages)
            return LLMResponse(content="ok")
    message = Message(content="explain main", user_id="owner", metadata={
        "workspace_context": "confidential stale snapshot", "snapshot_id": "old-wide"})
    context = await rig.runtime._contexts.build(
        message=message, session=rig.session, tools={}, skills={})
    await ModelAgent(Model()).run(message, context)
    assert seen
    assert "confidential" not in repr(seen)
    assert "old-wide" not in repr(seen)
    assert context.workspace is None and not context.memory and not context.knowledge


@pytest.mark.parametrize("kind", ["query", "context"])
@pytest.mark.parametrize("isolation", ["task", "principal", "session"])
async def test_concurrent_subjects_never_share_results(rig, captured, kind, isolation):
    from Sprout.orchestration.models import ExecutionNode, NodeType
    other = copy(rig)
    other.principal = (replace(rig.principal, user_id="second-owner")
                       if isolation == "principal" else rig.principal)
    other.session = (replace(rig.session, id="second-session", user_id=other.principal.user_id,
                            metadata={"entry_principal_v1": {
                                "user_id": other.principal.user_id, "source": "cli",
                                "authenticated": True}})
                     if isolation != "task" else rig.session)
    await rig.runtime._session_store.save_session(other.session)
    other.task = replace(rig.task, id="second-task", actor=other.principal,
                         metadata={"session_id": other.session.id},
                         delegation_scope=replace(rig.task.delegation_scope,
                                                  allowed_paths=(str(rig.root / "src/narrow"),)))
    await rig.storage.metadata.save_task(other.task)
    pending = ExecutionNode(id="second-node", task_id=other.task.id, type=NodeType.AGENT)
    await rig.storage.metadata.save_execution_node(pending)
    assert await rig.commission(pending, "second-owner", "")
    other.node = await rig.storage.metadata.get_execution_node(pending.id)
    async def run(subject, path):
        with node_scope(subject):
            tool = await issued(subject)
            with keys._project_analysis_call(tool):
                selected = await select(subject, path)
                await asyncio.sleep(0)
                return await consume(subject, selected, kind)
    first, second = await asyncio.gather(run(rig, "src/main.py"),
                                        run(other, "src/narrow/only.py"))
    assert "narrow" not in repr(first)
    assert "main" not in repr(second)
    assert "narrow" in repr(second)


@pytest.mark.parametrize("kind", ["query", "context"])
async def test_cancellation_drops_view_and_selection(rig, captured, monkeypatch, kind):
    original = rig.runtime._workspaces.analyze_view
    async def cancel(*args):
        await original(*args)
        raise asyncio.CancelledError
    monkeypatch.setattr(rig.runtime._workspaces, "analyze_view", cancel)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            selected = await select(rig)
            with pytest.raises(asyncio.CancelledError):
                await consume(rig, selected, kind)
            with pytest.raises(PermissionError):
                await consume(rig, selected, kind)


@pytest.mark.parametrize("operation", ["symbols", "knowledge", "dependencies", "neighbors"])
async def test_data_or_old_snapshot_cannot_replace_selector(rig, captured, operation):
    for value in ("snapshot-id", {"snapshot_id": "old-wide", "task_id": rig.task.id}, None):
        with pytest.raises((PermissionError, TypeError)):
            await rig.runtime._query_project_selection(value, {
                "workspace_id": rig.workspace.id, "operation": operation})
    assert captured.opens == []


@pytest.mark.parametrize("kind", ["query", "context"])
async def test_revocation_in_last_audit_callback_blocks_publication(
    rig, captured, monkeypatch, kind,
):
    original = rig.runtime._record_project_capture_policy
    async def revoke(request, decision):
        await original(request, decision)
        # Analysis already produced data; publication has not completed.
        if captured.spy.await_count:
            await rig.storage.metadata.save_task(replace(rig.task, delegation_scope=replace(
                rig.task.delegation_scope, allowed_paths=(str(rig.root / "src/narrow"),))))
    monkeypatch.setattr(rig.runtime, "_record_project_capture_policy", revoke)
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            with pytest.raises(PermissionError):
                await consume(rig, await select(rig), kind)


async def test_same_name_unrelated_plugin_is_not_disabled(rig):
    from Sprout.tools.result import ToolResult
    from Sprout.tools.spec import ToolSpec
    class Plugin:
        spec = ToolSpec(name="workspace_query", description="unrelated", input_schema={})
        async def invoke(self, arguments):
            return ToolResult.success("plugin-only")
    tool = Plugin()
    result = await rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True).execute(
        tool.spec.name, {})
    assert result.ok and result.content == "plugin-only"


async def test_query_policy_deny_precedes_capability_reason(rig):
    from Sprout.security.policy import SecurityPolicy
    tool = WorkspaceQueryTool()
    executor = rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
    executor.policy = SecurityPolicy(always_deny=frozenset({tool.spec.name}))
    result = await executor.execute(tool.spec.name, {})
    assert not result.ok and "security policy" in result.error


async def test_actual_agent_loop_receives_only_current_user_text(rig, monkeypatch):
    import Sprout.agent.loop as loop
    from Sprout.agent.loop import AgentLoop
    from Sprout.llm.messages import LLMResponse
    seen = []
    monkeypatch.setattr(loop, "get_agent_step_store", lambda: Mock())
    class Model:
        async def chat(self, messages, **kwargs):
            seen.extend(messages)
            return LLMResponse(content="ok")
    message = Message(user_id="owner", content="explain project", metadata={
        "workspace_context": "hidden-wide-fact", "resume_tool_calls": [{
            "id": "old", "name": "workspace_query", "arguments": {"snapshot_id": "old"}}]})
    context = await rig.runtime._contexts.build(
        message=message, session=rig.session, tools={}, skills={})
    await AgentLoop(Model()).run(message, context)
    assert seen and "hidden-wide-fact" not in repr(seen)
    assert not any(getattr(item, "role", "") == "tool" for item in seen)


async def test_isolation_drops_session_payload_but_preserves_numeric_budget(rig):
    session = replace(rig.session, metadata={"workspace_context": "hidden-shared-session"})
    message = Message(user_id="owner", content="hello", metadata={"task_budget": {
        "max_tool_calls": 1, "max_model_calls": 2, "max_tokens": 32,
        "hidden": "hidden-shared-session"}})
    context = await rig.runtime._contexts.build(
        message=message, session=session, tools={}, skills={})
    assert context.session.metadata == {}
    assert context.metadata == {"task_budget": {
        "max_tool_calls": 1, "max_model_calls": 2, "max_tokens": 32}}
    assert session.metadata  # No mutation of authoritative Session.
    assert "hidden-shared-session" not in repr(context)


async def test_foreign_workspace_rejected_before_capture(rig, captured):
    with node_scope(rig):
        tool = await issued(rig)
        with keys._project_analysis_call(tool):
            with pytest.raises(PermissionError):
                await rig.runtime._query_project_selection(await select(rig), {
                    "workspace_id": "other", "operation": "symbols"})
    assert not captured.opens


@pytest.mark.parametrize("mode", ["interactive", "cron", "automation"])
@pytest.mark.parametrize("subclass", [True, False])
async def test_default_query_subclasses_and_overlays_cannot_consume_approval(rig, mode, subclass):
    class Child(WorkspaceQueryTool):
        async def invoke(self, arguments):
            raise AssertionError("subclass invoke reached")
    tool = (Child if subclass else WorkspaceQueryTool)(AsyncMock())
    tool.spec = replace(tool.spec, risk_level="high")
    executor = rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
    executor.approvals = rig.runtime._approvals
    result = await executor.execute(tool.spec.name, {
        "workspace_id": rig.workspace.id, "operation": "symbols", "trusted": True,
        "approval_granted": True}, source=mode, task_id=rig.task.id,
        session_id=rig.session.id, requested_by=rig.principal.user_id)
    assert not result.ok and result.error == "workspace_query_scope_unavailable"
    assert executor.audit.record.called
    assert "workspace_query_scope_unavailable" in repr(executor.audit.record.call_args)
    assert "tool.denied" in repr(executor.events.publish.call_args_list).casefold()


async def test_explicit_internal_workspace_service_is_not_changed(rig):
    # Runtime/WorkspaceService host APIs remain present; do not execute their
    # unrestricted filesystem path through this restricted-model fixture.
    assert callable(rig.runtime.analyze_workspace)
    assert callable(rig.runtime._workspaces.analyze)
    assert callable(rig.runtime._workspaces.analyze_view)
