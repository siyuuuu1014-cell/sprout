"""Real SQLite images and B1 authority; secure-open substitute is not OS proof."""
import sqlite3
from dataclasses import replace
from unittest.mock import Mock

import pytest

from Sprout.agent.executor import ActionExecutor
from Sprout.agent.loop import AgentLoop
from Sprout.context.context import AgentContext
from Sprout.execution import file_access
from Sprout.execution.file_snapshot import read_fd
from Sprout.llm.messages import LLMResponse, ToolCall
from Sprout.message.models import Message
from Sprout.task.models import DelegationScope
from Sprout.tests.test_r04_pa2_b1_binding import node_scope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.tools.database_tool import DatabaseQueryTool
from Sprout.tools.executor import ToolExecutor


@pytest.fixture
async def dbcase(rig, monkeypatch):
    path = rig.root / "business.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE items(name TEXT, value INTEGER)")
        db.execute("INSERT INTO items VALUES('sample',7)")
    db.close()
    rig.task = replace(rig.task, delegation_scope=DelegationScope(
        allowed_actions=frozenset({"tool.invoke", "db.read"}), allowed_paths=(str(path),)))
    await rig.storage.metadata.save_task(rig.task)
    opens = []
    def capture(root, target, operation, authorize, *, content, **kwargs):
        assert root == rig.root and target == path and operation == "snapshot"
        authorize()
        opens.append(target)
        with target.open("rb") as stream:
            return read_fd(stream.fileno(), content)
    monkeypatch.setattr(file_access, "file_operation", capture)
    return rig, path, opens


async def invoke(case, sql, **extra):
    rig, path, _ = case
    with node_scope(rig):
        return await rig.runtime.tools.get("database_query").invoke({
            "database": str(path), "sql": sql, **extra})


@pytest.mark.parametrize("sql,expected", [
    ("SELECT name,value FROM items", "sample"),
    ("SELECT name FROM sqlite_schema WHERE type='table'", "items"),
    ("PRAGMA table_info(items)", "value"),
    ("WITH selected AS (SELECT * FROM items) SELECT value FROM selected", "7"),
])
async def test_legal_read(dbcase, sql, expected):
    result = await invoke(dbcase, sql)
    assert result.ok and expected in result.content
    assert len(dbcase[2]) == 1


@pytest.mark.parametrize("sql", [
    "UPDATE items SET value=8", "DELETE FROM items", "DROP TABLE items",
    "CREATE TABLE forbidden(value)", "INSERT INTO items VALUES('bad',9)",
    "WITH x AS (SELECT 1) DELETE FROM items", "PRAGMA user_version=3",
    "ATTACH DATABASE ':memory:' AS other", "SELECT load_extension('forbidden')",
])
async def test_read_cannot_mutate_or_attach(dbcase, sql):
    result = await invoke(dbcase, sql)
    assert not result.ok
    with sqlite3.connect(dbcase[1]) as db:
        assert db.execute("SELECT * FROM items").fetchall() == [("sample", 7)]
        assert db.execute("PRAGMA user_version").fetchone() == (0,)


async def test_model_fields_do_not_supply_binding(dbcase):
    rig, path, opens = dbcase
    result = await rig.runtime.tools.get("database_query").invoke({
        "database": str(path), "sql": "SELECT 1", "task_id": rig.task.id,
        "trusted": True, "_sprout_context": {"task_id": rig.task.id}})
    assert not result.ok and not opens
    unbound = DatabaseQueryTool(rig.runtime._database_broker)
    with node_scope(rig):
        assert not (await unbound.invoke({"database": str(path), "sql": "SELECT 1"})).ok


@pytest.mark.parametrize("actions", [{"tool.invoke"}, {"db.read"}])
async def test_workspace_and_scope_denial_before_open(dbcase, actions):
    rig, path, opens = dbcase
    assert not (await invoke(dbcase, "SELECT 1", workspace_id="other")).ok
    rig.task = replace(rig.task, delegation_scope=DelegationScope(
        allowed_actions=frozenset(actions), allowed_paths=(str(path),)))
    await rig.storage.metadata.save_task(rig.task)
    assert not (await invoke(dbcase, "SELECT 1")).ok
    assert not opens


async def test_real_actor_task_and_scope_reach_broker(dbcase, monkeypatch):
    rig, _, _ = dbcase
    requests = []
    decide = rig.runtime._database_broker._policy.decide
    def record(request):
        requests.append(request)
        return decide(request)
    monkeypatch.setattr(rig.runtime._database_broker._policy, "decide", record)
    assert (await invoke(dbcase, "SELECT * FROM items")).ok
    expected = rig.task.delegation_scope.bind_paths(str(rig.root)).narrow(
        rig.task.delegation_scope.bind_paths(str(rig.root)))
    assert requests and all(r.actor == rig.principal and r.task_id == rig.task.id
                            and r.scope == expected for r in requests)


async def test_real_tool_result_reaches_agent(dbcase, monkeypatch):
    rig, path, _ = dbcase
    seen = []
    class Model:
        async def chat(self, messages, **kwargs):
            seen.append(list(messages))
            if len(seen) == 1:
                return LLMResponse(tool_calls=(ToolCall(id="database-result", name="database_query",
                    arguments={"database": str(path), "sql": "SELECT name,value FROM items"}),))
            return LLMResponse(content="DATABASE_AGENT_RESULT")
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: Mock())
    tool = rig.runtime.tools.get("database_query")
    executor = ToolExecutor(rig.runtime.tools).with_scoped_tools(
        {tool.spec.name: tool}, strict=True)
    with node_scope(rig):
        result = await AgentLoop(Model(), executor=ActionExecutor(executor)).run(
            Message(content="query database"),
            AgentContext(rig.session, tools={tool.spec.name: tool}))
    assert result.content == "DATABASE_AGENT_RESULT"
    assert any(str(m.role) == "tool" and "sample" in m.content and "7" in m.content
               for m in seen[-1])


async def test_revocation_before_publication_discards_rows(dbcase, monkeypatch):
    rig, _, _ = dbcase
    query = rig.runtime._database_broker.query
    async def revoke(*args, **kwargs):
        result = await query(*args, **kwargs)
        await rig.storage.metadata.save_task(replace(rig.task, delegation_scope=replace(
            rig.task.delegation_scope, denied_actions=frozenset({"db.read"}))))
        return result
    monkeypatch.setattr(rig.runtime._database_broker, "query", revoke)
    result = await invoke(dbcase, "SELECT * FROM items")
    assert not result.ok and "sample" not in result.content


async def test_host_write_boundary_and_parameterized_read(dbcase):
    rig, path, _ = dbcase
    scope = replace(rig.task.delegation_scope,
                    allowed_actions=frozenset({"db.read", "db.write"}))
    broker = rig.runtime._database_broker
    refused = await broker.execute(rig.workspace, path, "UPDATE items SET value=8",
        task_id=rig.task.id, scope=scope)
    assert not refused.allowed
    written = await broker.execute(rig.workspace, path, "UPDATE items SET value=9",
        sandbox=True, task_id=rig.task.id, scope=scope)
    assert written.allowed  # Existing host sandbox flag, not a model write capability.
    result = await broker.query(rig.workspace, path, "SELECT value FROM items WHERE name=?",
        ("sample",), task_id=rig.task.id, scope=scope, actor=rig.principal)
    assert result.allowed and result.rows == ((9,),)
