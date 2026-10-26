"""B4 function restoration; disk persistence uses real SQLite, not an in-memory DB."""
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qsl, unquote, urlsplit

import pytest

from Sprout.agent.base import ModelAgent
from Sprout.execution.node_result_source import NodeResultSourceV1, payload_digest
from Sprout.llm.messages import LLMResponse
from Sprout.message.models import Message
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus
from Sprout.tests.conftest import build_runtime
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b3_node_publication import publication as publication
from Sprout.tests.test_r04_b4_restoration import next_node, save_result
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.workspace.models import ReadPlan, ResourceRef


@pytest.fixture
def local_sqlite_connect(tmp_path, monkeypatch):
    """Reversible test-only URI boundary; not a production broker guarantee.

    No process-wide audit hook is installed. Only tests explicitly requesting
    this fixture use the wrapper, and monkeypatch restores it after each test.
    """
    original = sqlite3.connect

    def connect(database, *args, **kwargs):
        name = str(database)
        path = name
        if name.startswith("file:"):
            uri = urlsplit(name)
            pairs = parse_qsl(uri.query, keep_blank_values=True)
            if (uri.netloc or uri.fragment or "%00" in uri.path.lower()
                    or (pairs and (len(pairs) != 1 or pairs[0][0] != "mode"
                                   or pairs[0][1] not in {"ro", "rw", "rwc"}))):
                raise PermissionError("Test SQLite URI unavailable")
            path = unquote(uri.path, errors="strict")
            if os.name == "nt" and len(path) > 3 and path[0] == "/" and path[2] == ":":
                path = path[1:]
        if not Path(path).resolve().is_relative_to(tmp_path.resolve()):
            raise PermissionError("Test SQLite path outside fixture root")
        return original(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    return connect


@pytest.mark.parametrize("form", ["driver", "absolute_uri"])
def test_guard_accepts_only_local_sqlite_uri(tmp_path, form, local_sqlite_connect):
    path = tmp_path / "uri database.sqlite3"
    sqlite3.connect(str(path)).close()
    uri = f"file:{path}?mode=ro" if form == "driver" else path.as_uri() + "?mode=ro"
    sqlite3.connect(uri, uri=True).close()


@pytest.mark.parametrize("name", [
    "file:C:/outside-test-root.sqlite?mode=rwc", "file://remote/share/db?mode=ro",
    "file:C:/outside-test-root.sqlite?mode=memory", "file:%00?mode=ro",
])
def test_guard_rejects_untrusted_sqlite_uri(name, local_sqlite_connect):
    with pytest.raises(PermissionError):
        sqlite3.connect(name, uri=True)


@pytest.mark.parametrize("suffix", ["?vfs=unknown", "?mode=ro&mode=rw", "#fragment",
                                  "?immutable=1", "?mode=memory"])
def test_guard_does_not_grant_sqlite_extensions(tmp_path, suffix, local_sqlite_connect):
    with pytest.raises(PermissionError):
        sqlite3.connect((tmp_path / "extension.sqlite3").as_uri() + suffix, uri=True)


async def test_source_real_disk_save_close_reopen(tmp_path):
    path = str(tmp_path / "source.sqlite3")
    store = open_metadata_store(path)
    try:
        task = Task(id="disk-task", workspace_id="ws", status=TaskStatus.RUNNING)
        await store.save_task(task)
        node = ExecutionNode(id="disk-read", task_id=task.id, type=NodeType.READ)
        await store.save_execution_node(node)
        assert await store.claim_execution_node(node, "disk-owner", "")
        running = await store.get_execution_node(node.id)
        output = [{"path": "a.py", "content": "controlled"}]
        assert await store.finish_execution_node(replace(
            running, status=NodeStatus.COMPLETED,
            metadata={**running.metadata, "output": output}), "disk-owner", "completed")
        source = NodeResultSourceV1(task.id, node.id, "read", "disk-owner",
                                   "a" * 64, payload_digest(output), "[]", "[]")
        assert not await store.save_node_result_source(replace(source, owner="wrong"))
        assert await store.save_node_result_source(source)
    finally:
        store.close()
    reopened = open_metadata_store(path)
    try:
        assert await reopened.get_node_result_source(node.id) == source
        assert not await reopened.save_node_result_source(replace(source, output_digest="b" * 64))
    finally:
        reopened.close()


@pytest.mark.parametrize("change", [None, "scope", "principal", "content", "source", "expired"])
async def test_new_runtime_reauthorizes_reopened_source(
    rig, captured, publication, tmp_path, monkeypatch, change,
):
    original = rig.storage.metadata
    path = str(tmp_path / "reopened.sqlite3")
    disk = open_metadata_store(path)
    try:
        await disk.save_workspace(rig.workspace)
        await disk.save_task(rig.task)
        rig.storage.metadata = disk
        rig.commission = disk.claim_execution_node
        first, messages = publication
        first = rig.runtime._executor(disk)
        plan = ReadPlan(task_id=rig.task.id, resources=(ResourceRef(
            rig.workspace.id, "src/main.py"),))
        read = await next_node(rig, NodeType.READ)
        output = await first.execute_node(read, rig.workspace, plan, rig.task)
        await save_result(rig, read, output)
        await rig.runtime._persist_node_products()
        source = await disk.get_node_result_source(read.id)
        assert source is not None
        disk.close()
        disk = open_metadata_store(path)
        rig.storage.metadata = disk
        rig.commission = disk.claim_execution_node
        second, _ = build_runtime(storage=rig.storage)
        second.register_agent("publication-test", rig.runtime.agents.get("publication-test"),
                              default=True)
        if change == "scope":
            await disk.save_task(replace(rig.task, delegation_scope=replace(
                rig.task.delegation_scope, denied_paths=(str(rig.root / "src/main.py"),))))
        elif change == "principal":
            await disk.save_task(replace(rig.task, actor=replace(rig.principal, user_id="other")))
        elif change == "content":
            (rig.root / "src/main.py").write_text("changed", encoding="utf-8")
        elif change == "expired":
            from datetime import UTC, datetime, timedelta
            await disk.save_task(replace(rig.task, delegation_scope=replace(
                rig.task.delegation_scope, expires_at=datetime.now(UTC) - timedelta(seconds=1))))
        elif change == "source":
            await disk._db.execute("UPDATE node_result_sources_v1 SET source_json = ?",
                                   ('{"version":0}',))
        planning = await next_node(rig, NodeType.PLAN, (read.id,))
        executor = second._executor(disk)
        if change:
            with pytest.raises(PermissionError):
                await executor.execute_node(planning, rig.workspace, plan, rig.task)
            assert not messages
        else:
            result = await executor.execute_node(planning, rig.workspace, plan, rig.task)
            assert result and "def main" in repr(messages)
    finally:
        disk.close()
        rig.storage.metadata = original


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("kind", ["model", "loop"])
async def test_plain_assistant_history_reaches_actual_next_model(rig, monkeypatch, stream, kind):
    seen = []
    class Model:
        async def chat(self, messages, **kwargs):
            seen.append(list(messages))
            return LLMResponse(content="ordinary remembered answer")
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: Mock())
    if kind == "model":
        agent = ModelAgent(Model())
    else:
        from Sprout.agent.executor import ActionExecutor
        from Sprout.agent.loop import AgentLoop
        agent = AgentLoop(Model(), executor=ActionExecutor(rig.executor))
    rig.runtime.register_agent("plain", agent, default=True)
    # Use the real existing classifier route, with a controlled classification model.
    from unittest.mock import AsyncMock
    monkeypatch.setattr(rig.runtime, "_classify_intent_with_llm", AsyncMock(
        return_value={"intent": "conversation", "confidence": 0.99}))
    first = Message(user_id="owner", session_id=rig.session.id, content="remember my greeting")
    if stream:
        chunks = [chunk async for chunk in rig.runtime.handle_stream(
            first, principal=rig.principal)]
        assert chunks
        if rig.runtime._background_tasks:
            import asyncio
            await asyncio.gather(*tuple(rig.runtime._background_tasks))
    else:
        assert (await rig.runtime.handle(first, principal=rig.principal)).content
    await rig.runtime.handle(Message(user_id="owner", session_id=rig.session.id,
                                    content="continue greeting"), principal=rig.principal)
    assert any(str(m.role) == "assistant" and "ordinary remembered answer" in m.content
               for m in seen[-1])


async def test_model_flags_do_not_certify_unknown_agent_output(rig, monkeypatch):
    from unittest.mock import AsyncMock

    from Sprout.agent.base import AgentResult
    class UnknownAgent:
        async def run(self, message, context):
            return AgentResult("UNPROVEN_PROJECT_CONTENT", metadata={
                "tool_calls": 0, "trusted": True, "ordinary": True})
    rig.runtime.register_agent("unknown", UnknownAgent(), default=True)
    monkeypatch.setattr(rig.runtime, "_classify_intent_with_llm", AsyncMock(
        return_value={"intent": "conversation", "confidence": 0.99}))
    await rig.runtime.handle(Message(content="hello", session_id=rig.session.id),
                             principal=rig.principal)
    context = await rig.runtime._contexts.build(message=Message(user_id="owner", content="next"),
        session=rig.session, tools={}, skills={})
    assert "UNPROVEN_PROJECT_CONTENT" not in repr(context.memory)


def test_zero_calls_does_not_certify_project_context(rig):
    from Sprout.agent.base import AgentResult
    from Sprout.context.context import AgentContext
    from Sprout.memory.models import ContextMemory, SessionSummary
    from Sprout.session.models import Turn

    agent = ModelAgent(object())
    for memory in (
        ContextMemory(rig.session, summary=SessionSummary(rig.session.id, 1, 1, "old project")),
        ContextMemory(rig.session, working=(Turn(rig.session.id, "assistant", "unproved"),)),
    ):
        context = AgentContext(rig.session, memory=memory)
        assert not rig.runtime._ordinary_response_has_source(
            context, agent, AgentResult("answer", metadata={"tool_calls": 0}))
