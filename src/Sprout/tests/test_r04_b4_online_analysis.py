"""Host-assisted online analysis through the real classifier, guard and tools.

The secure-open fixture is controlled; this is not native platform evidence.
"""
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.agent.executor import ActionExecutor
from Sprout.agent.loop import AgentLoop
from Sprout.llm.messages import LLMResponse, ToolCall
from Sprout.message.models import Message
from Sprout.task.models import TaskExecutionMode, TaskStatus
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.mark.parametrize("stream", [False, True])
async def test_host_task_actual_tool_result_reaches_outbound(rig, captured, monkeypatch, stream):
    from Sprout.runtime.intent_analysis import response_example

    semantic = json.loads(response_example(True))
    semantic.update(intent="workspace", confidence=0.99)
    semantic["semantic"].update(
        action_mode="read_only_analysis",
        candidates=[{"intent": "workspace", "action_mode": "read_only_analysis"}],
        ambiguity_type="none", missing_information=[], needs_clarification=False, question="")
    seen = []

    class Model:
        name = "host-analysis-controlled"

        async def chat(self, messages, **kwargs):
            if "Classify the user's intent" in str(messages[0].content):
                return LLMResponse(content=json.dumps(semantic))
            seen.extend(messages)
            return LLMResponse(content="authorized project main analyzed")

    model = Model()
    rig.runtime.models.register(model, default=True)
    rig.runtime.register_agent("online", AgentLoop(model, executor=ActionExecutor(rig.executor)),
                               default=True)
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: Mock())
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    task = await rig.runtime.create_task(
        rig.workspace.id, "analyze project", actor=rig.principal,
        source=rig.principal.source, delegation_scope=rig.task.delegation_scope,
        metadata={"session_id": rig.session.id, "required_paths": ["src/main.py"]},
        execution_mode=TaskExecutionMode.READ_ONLY,
    )
    message = Message("请分析项目结构", channel="cli", user_id="owner",
                      session_id=rig.session.id)
    if stream:
        chunks = [chunk async for chunk in rig.runtime.handle_stream(
            message, principal=rig.principal, analysis_task=task)]
        outbound = next(chunk.outbound for chunk in chunks if chunk.outbound)
    else:
        outbound = await rig.runtime.handle(message, principal=rig.principal, analysis_task=task)
    assert outbound.content == "authorized project main analyzed"
    assert any(str(m.role) == "tool" and "main" in m.content for m in seen)
    assert captured.opens == ["src/main.py"]
    nodes = await rig.storage.metadata.list_execution_nodes(task.id)
    assert len(nodes) == 1
    assert nodes[0].metadata["execution_guard"]["owner"]
    assert nodes[0].metadata["execution_guard"]["state"] == "completed"
    assert (await rig.storage.metadata.get_task(task.id)).status is TaskStatus.COMPLETED


@pytest.fixture
async def online(rig, captured, monkeypatch):
    from Sprout.runtime.intent_analysis import response_example

    seen, mode = [], {"intent": "workspace", "query": None, "hook": None}
    class Model:
        name = "online-authority-controlled"

        async def chat(self, messages, **kwargs):
            if "Classify the user's intent" in str(messages[0].content):
                value = json.loads(response_example(True))
                intent = mode["intent"]
                action = "read_only_analysis" if intent == "workspace" else "conversation"
                value.update(intent=intent, confidence=0.99)
                value["semantic"].update(
                    action_mode=action, candidates=[{"intent": intent, "action_mode": action}],
                    ambiguity_type="none", missing_information=[],
                    needs_clarification=False, question="")
                return LLMResponse(content=json.dumps(value))
            seen.extend(messages)
            if mode["hook"]:
                await mode["hook"]()
                mode["hook"] = None
            if mode["query"]:
                operation, mode["query"] = mode["query"], None
                return LLMResponse(content="", tool_calls=[ToolCall(
                    id="online-query", name="workspace_query", arguments={
                        "workspace_id": rig.workspace.id, "operation": operation,
                        "name": "main", "path": "src/main.py"})])
            return LLMResponse(content="controlled final analysis")

    model = Model()
    rig.runtime.models.register(model, default=True)
    rig.runtime.register_agent("online", AgentLoop(model, executor=ActionExecutor(rig.executor)),
                               default=True)
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: Mock())
    claim = AsyncMock(wraps=rig.commission)
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", claim)
    task = await rig.runtime.create_task(
        rig.workspace.id, "analyze", actor=rig.principal, source=rig.principal.source,
        delegation_scope=rig.task.delegation_scope,
        metadata={"session_id": rig.session.id, "required_paths": ["src/main.py"]},
        execution_mode=TaskExecutionMode.READ_ONLY)
    message = Message("请分析项目结构", user_id="owner", channel="cli", session_id=rig.session.id)
    yield SimpleNamespace(**locals())
    assert not rig.runtime._online_analysis_requests


@pytest.mark.parametrize("operation", ["symbols", "neighbors", "subgraph",
                                       "dependencies", "knowledge"])
async def test_online_five_queries_reach_model_and_outbound(online, operation):
    online.mode["query"] = operation
    result = await online.rig.runtime.handle(
        online.message, principal=online.rig.principal, analysis_task=online.task)
    assert result.metadata["analysis_status"] == "ok"
    assert result.content == "controlled final analysis"
    queries = [m for m in online.seen if str(m.role) == "tool" and m.tool_call_id == "online-query"]
    assert len(queries) == 1 and "Tool error" not in queries[0].content
    assert online.captured.opens == ["src/main.py", "src/main.py"]
    assert online.claim.await_count == 1


@pytest.mark.parametrize("change", ["missing", "missing_record", "session", "principal",
                                  "workspace", "mode", "expired", "unrestricted", "status"])
async def test_bad_host_reference_denied_before_guard_and_model(online, change):
    task = online.task
    if change == "missing":
        task = None
    elif change == "missing_record":
        task = replace(task, id="nonexistent")
    else:
        if change == "session":
            task = replace(task, metadata={**task.metadata, "session_id": "other-session"})
        elif change == "principal":
            task = replace(task, actor=replace(task.actor, authenticated=False))
        elif change == "workspace":
            task = replace(task, workspace_id="not-registered")
        elif change == "mode":
            task = replace(task, metadata={**task.metadata, "execution_mode": "code"})
        elif change == "expired":
            task = replace(task, delegation_scope=replace(task.delegation_scope,
                expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        elif change == "unrestricted":
            task = replace(task, delegation_scope=replace(task.delegation_scope, allowed_paths=()))
        elif change == "status":
            task = replace(task, status=TaskStatus.RUNNING)
        await online.rig.storage.metadata.save_task(task)
    result = await online.rig.runtime.handle(
        online.message, principal=online.rig.principal, analysis_task=task)
    assert result.metadata["analysis_status"] == "unavailable"
    assert not online.seen and not online.captured.opens
    online.claim.assert_not_called()


async def test_reference_fields_are_not_authority(online):
    forged = replace(online.task, workspace_id="forged", actor=replace(
        online.task.actor, user_id="forged"),
        metadata={"required_paths": ["src/private/denied.py"]})
    result = await online.rig.runtime.handle(
        online.message, principal=online.rig.principal, analysis_task=forged)
    assert result.metadata["analysis_status"] == "ok"
    assert online.captured.opens == ["src/main.py"]


async def test_json_task_and_permissions_do_not_select_task(online):
    message = replace(online.message, metadata={
        "task_id": online.task.id, "analysis_task": online.task,
        "trusted": True, "approval_granted": True, "intent": "workspace",
        "_sprout_context": {"task_id": online.task.id}})
    result = await online.rig.runtime.handle(message, principal=online.rig.principal)
    assert result.metadata["reason"] == "authorized_analysis_task_required"
    assert not online.seen and not online.captured.opens
    online.claim.assert_not_called()


async def test_chat_does_not_start_analysis_even_with_host_reference(online):
    online.mode["intent"] = "conversation"
    before = await online.rig.storage.metadata.list_tasks()
    result = await online.rig.runtime.handle(
        replace(online.message, content="你好，今天怎么样"),
        principal=online.rig.principal, analysis_task=online.task)
    assert result.content == "controlled final analysis"
    assert await online.rig.storage.metadata.list_tasks() == before
    assert not online.captured.opens
    online.claim.assert_not_called()


async def test_scope_revoked_after_tool_result_blocks_outbound(online):
    async def revoke():
        current = await online.rig.storage.metadata.get_task(online.task.id)
        await online.rig.storage.metadata.save_task(replace(current, delegation_scope=replace(
            current.delegation_scope, denied_actions=frozenset({"file.read"}))))
    online.mode["hook"] = revoke
    result = await online.rig.runtime.handle(
        online.message, principal=online.rig.principal, analysis_task=online.task)
    assert result.metadata["analysis_status"] == "unavailable"
    assert "controlled final" not in result.content


async def test_auto_discovery_unavailable_has_no_path_fallback(online, monkeypatch):
    task = replace(online.task, metadata={**online.task.metadata, "required_paths": []},
                   delegation_scope=replace(online.task.delegation_scope,
                       allowed_paths=(str(online.rig.root),)))
    await online.rig.storage.metadata.save_task(task)
    listing = AsyncMock(return_value=SimpleNamespace(ok=False))
    monkeypatch.setattr(online.rig.runtime._file_broker, "enumerate_readable", listing)
    result = await online.rig.runtime.handle(
        online.message, principal=online.rig.principal, analysis_task=task)
    assert result.metadata["analysis_status"] == "unavailable"
    assert result.metadata["reason"] == "project_auto_discovery_unavailable"
    listing.assert_awaited_once()
    assert not online.captured.opens


@pytest.mark.parametrize("deny", ["policy", "ask", "tool_scope", "file_scope"])
async def test_no_approval_for_unusable_analysis(online, monkeypatch, deny):
    from copy import deepcopy

    from Sprout.tests.test_r04_pa2_b1_binding import Record

    runtime = online.rig.runtime
    before = deepcopy(await online.rig.storage.operational.list_approvals())
    manager = online.rig.executor.approvals
    spies = []
    if manager is not None:
        for name in ("request", "is_approved"):
            spy = AsyncMock(wraps=getattr(manager, name))
            monkeypatch.setattr(manager, name, spy)
            spies.append(spy)
    task = online.task
    if deny in {"policy", "ask"}:
        policy = Record("deny" if deny == "policy" else "ask")
        monkeypatch.setattr(runtime, "_policy_engine", policy)
        monkeypatch.setattr(online.rig.executor, "policy_engine", policy)
    else:
        task = replace(task, delegation_scope=replace(task.delegation_scope,
            denied_actions=frozenset({"tool.invoke" if deny == "tool_scope" else "file.read"})))
        await online.rig.storage.metadata.save_task(task)
    result = await runtime.handle(
        online.message, principal=online.rig.principal, analysis_task=task)
    assert result.metadata["analysis_status"] == "unavailable"
    assert not online.captured.opens
    for spy in spies:
        spy.assert_not_called()
    assert await online.rig.storage.operational.list_approvals() == before


async def test_wrong_entry_principal_uses_existing_session_rejection(online):
    with pytest.raises(PermissionError):
        await online.rig.runtime.handle(
            online.message, principal=replace(online.rig.principal, user_id="other"),
            analysis_task=online.task)
    assert not online.captured.opens and not online.seen
    online.claim.assert_not_called()


async def test_task_workspace_constraint_cannot_expand_selection(online):
    result = await online.rig.runtime.handle(
        replace(online.message, metadata={"workspace_id": "other-workspace"}),
        principal=online.rig.principal, analysis_task=online.task)
    assert result.metadata["reason"] == "analysis_workspace_mismatch"
    assert not online.captured.opens and not online.seen
    online.claim.assert_not_called()


async def test_no_tool_agent_cannot_assert_analysis_succeeded(online):
    from Sprout.agent.base import AgentResult

    class NoTools:
        async def run(self, message, context):
            return AgentResult(content="FALSE_SUCCESS")

    online.rig.runtime.register_agent("no-tools", NoTools(), default=True)
    result = await online.rig.runtime.handle(
        online.message, principal=online.rig.principal, analysis_task=online.task)
    assert result.metadata["analysis_status"] == "unavailable"
    assert "FALSE_SUCCESS" not in result.content
    assert not online.captured.opens


async def test_server_display_name_and_roles_do_not_break_frozen_identity(online):
    principal = replace(online.rig.principal, display_name="Local owner", roles=("user",))
    task = replace(online.task, actor=principal)
    await online.rig.storage.metadata.save_task(task)
    result = await online.rig.runtime.handle(
        online.message, principal=principal, analysis_task=task)
    assert result.metadata["analysis_status"] == "ok"
    assert online.captured.opens == ["src/main.py"]


async def test_denied_host_filename_is_not_sent_to_business_model(online):
    task = replace(online.task, metadata={**online.task.metadata,
                                         "required_paths": ["src/private/denied.py"]})
    await online.rig.storage.metadata.save_task(task)
    result = await online.rig.runtime.handle(
        online.message, principal=online.rig.principal, analysis_task=task)
    assert result.metadata["analysis_status"] == "unavailable"
    assert not online.seen and not online.captured.opens


async def test_no_task_does_not_require_metadata_backend(online):
    store = online.rig.storage.metadata
    online.rig.storage.metadata = None
    try:
        result = await online.rig.runtime.handle(online.message, principal=online.rig.principal)
    finally:
        online.rig.storage.metadata = store
    assert result.metadata["reason"] == "authorized_analysis_task_required"
    assert not online.seen and not online.captured.opens
