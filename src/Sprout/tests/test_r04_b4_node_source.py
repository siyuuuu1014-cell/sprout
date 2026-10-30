"""Side records require real settled ownership; old results are not upgraded."""
import json
from dataclasses import replace

import pytest

from Sprout.execution.node_result_source import NodeResultSourceV1, payload_digest
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b3_node_publication import publication as publication
from Sprout.tests.test_r04_b4_restoration import next_node, save_result
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.workspace.models import ReadPlan, ResourceRef


@pytest.mark.parametrize("mutation", [None, "owner", "payload", "task", "version", "active"])
async def test_settled_source_cas(mutation):
    store = open_metadata_store(":memory:")
    try:
        task = Task(id="task", workspace_id="ws", status=TaskStatus.RUNNING)
        await store.save_task(task)
        pending = ExecutionNode(id="read", task_id=task.id, type=NodeType.READ)
        await store.save_execution_node(pending)
        assert await store.claim_execution_node(pending, "actual-owner", "")
        running = await store.get_execution_node(pending.id)
        output = [{"path": "a.py", "content": "safe"}]
        if mutation != "active":
            assert await store.finish_execution_node(replace(
                running, status=NodeStatus.COMPLETED,
                metadata={**running.metadata, "output": output}), "actual-owner", "completed")
        source = NodeResultSourceV1(task.id, pending.id, "read", "actual-owner",
                                   "a" * 64, payload_digest(output), "[]", "[]")
        if mutation == "owner":
            source = replace(source, owner="fake")
        elif mutation == "payload":
            source = replace(source, output_digest="b" * 64)
        elif mutation == "task":
            source = replace(source, task_id="other")
        elif mutation == "version":
            source = replace(source, version=2)
        assert await store.get_node_result_source(pending.id) is None
        assert await store.save_node_result_source(source) is (mutation is None)
        if mutation is None:
            assert await store.get_node_result_source(pending.id) == source
            assert await store.save_node_result_source(source)
            assert not await store.save_node_result_source(replace(source, subject_digest="b" * 64))
    finally:
        store.close()


def test_unknown_or_incomplete_source_is_not_legacy_upgrade():
    with pytest.raises((ValueError, TypeError)):
        NodeResultSourceV1.from_json(json.dumps({"version": 0}))


@pytest.mark.parametrize("change", [None, "content", "scope", "principal"])
async def test_persisted_read_revalidated_before_actual_model(rig, captured, publication, change):
    executor, messages = publication
    plan = ReadPlan(task_id=rig.task.id, resources=(ResourceRef(rig.workspace.id, "src/main.py"),))
    read = await next_node(rig, NodeType.READ)
    output = await executor.execute_node(read, rig.workspace, plan, rig.task)
    await save_result(rig, read, output)
    await rig.runtime._persist_node_products()
    assert await rig.storage.metadata.get_node_result_source(read.id) is not None
    # Recreate the consumer registry: only Metadata provenance may now bridge calls.
    rig.runtime._node_products.clear()
    if change == "content":
        (rig.root / "src/main.py").write_text("CHANGED", encoding="utf-8")
    elif change == "scope":
        rig.task = replace(rig.task, delegation_scope=replace(
            rig.task.delegation_scope, denied_paths=(str(rig.root / "src/main.py"),)))
        await rig.storage.metadata.save_task(rig.task)
    elif change == "principal":
        rig.task = replace(rig.task, actor=replace(rig.task.actor, user_id="other"))
        await rig.storage.metadata.save_task(rig.task)
    planning = await next_node(rig, NodeType.PLAN, (read.id,))
    if change:
        with pytest.raises(PermissionError):
            await executor.execute_node(planning, rig.workspace, plan, rig.task)
        assert not messages
    else:
        result = await executor.execute_node(planning, rig.workspace, plan, rig.task)
        assert result
        assert "def main" in repr(messages)
        await save_result(rig, planning, result)
        await rig.runtime._persist_node_products()
        rig.runtime._node_products.clear()
        sub = await next_node(rig, NodeType.SUBTASK, (planning.id,),
                              {"description": "inspect main", "index": 0, "kind": "analysis"})
        finding = await executor.execute_node(sub, rig.workspace, plan, rig.task)
        await save_result(rig, sub, finding)
        await rig.runtime._persist_node_products()
        origin = await rig.storage.metadata.get_node_result_source(sub.id)
        assert {p["node_id"] for p in json.loads(origin.parents_json)} == {read.id, planning.id}
        rig.runtime._node_products.clear()
        agent = await next_node(rig, NodeType.AGENT, (sub.id,))
        answer = await executor.execute_node(agent, rig.workspace, plan, rig.task)
        assert answer["content"] == "plain response"
        assert "Findings from the plan" in repr(messages)


async def test_new_runtime_reissues_subject_for_persisted_source(rig, captured, publication):
    from Sprout.tests.conftest import build_runtime

    first, messages = publication
    plan = ReadPlan(task_id=rig.task.id, resources=(ResourceRef(rig.workspace.id, "src/main.py"),))
    read = await next_node(rig, NodeType.READ)
    output = await first.execute_node(read, rig.workspace, plan, rig.task)
    await save_result(rig, read, output)
    await rig.runtime._persist_node_products()
    second, _ = build_runtime(storage=rig.storage)
    second.register_agent("publication-test", rig.runtime.agents.get("publication-test"),
                          default=True)
    assert not second._node_products
    assert not second._project_analysis_tools
    planning = await next_node(rig, NodeType.PLAN, (read.id,))
    result = await second._executor(rig.storage.metadata).execute_node(
        planning, rig.workspace, plan, rig.task)
    assert result
    assert "def main" in repr(messages)
    assert captured.opens == ["src/main.py", "src/main.py", "src/main.py"]


async def test_bound_project_tool_results_keep_complete_source_footprint(
    rig, captured, monkeypatch,
):
    from unittest.mock import Mock

    from Sprout.agent.executor import ActionExecutor
    from Sprout.agent.loop import AgentLoop
    from Sprout.llm.messages import LLMResponse, ToolCall

    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: Mock())
    seen = []
    class Model:
        async def chat(self, messages, **kwargs):
            seen.append(messages)
            if len(seen) <= 2:
                path = "src/main.py" if len(seen) == 1 else "src/worker.py"
                return LLMResponse(tool_calls=(ToolCall(
                    id=path, name="project_analyze", arguments={"path": str(rig.root / path)}),))
            return LLMResponse(content="derived from both files")
    rig.runtime.register_agent("project-loop", AgentLoop(Model(),
        executor=ActionExecutor(rig.executor)), default=True)
    plan = ReadPlan(task_id=rig.task.id, resources=tuple(
        ResourceRef(rig.workspace.id, path) for path in ("src/main.py", "src/worker.py")))
    output = await rig.runtime._executor(rig.storage.metadata).execute_node(
        rig.node, rig.workspace, plan, rig.task)
    assert output["content"] == "derived from both files"
    assert await rig.storage.metadata.finish_execution_node(replace(
        rig.node, status=NodeStatus.COMPLETED,
        metadata={**rig.node.metadata, "output": output}), "fixture-owner", "completed")
    await rig.runtime._persist_node_products()
    source = await rig.storage.metadata.get_node_result_source(rig.node.id)
    assert source is not None
    assert {r["path"] for r in json.loads(source.resources_json)} == {
        "src/main.py", "src/worker.py"}
