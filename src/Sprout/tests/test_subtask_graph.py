"""PLAN -> SUBTASK -> AGENT decomposition.

A strategy plan's structured steps compile into read-only SUBTASK nodes that
sit between PLAN and the first edit round. They all depend only on PLAN, so
the scheduler may run the analysis ones concurrently, and the first AGENT
waits for every one of them so their evidence reaches the prompt.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from Sprout.orchestration.compiler import ExecutionGraphBuilder
from Sprout.orchestration.models import NodeType
from Sprout.runtime.nodes import NodeExecutor
from Sprout.task.models import Task
from Sprout.workspace.models import ReadPlan, WorkspaceManifest

STEPS = (
    {"description": "读现有实现", "kind": "analysis", "target_paths": ["src/a.py"]},
    {"description": "改代码", "kind": "edit", "target_paths": ["src/a.py"]},
)


def _graph(steps=()):
    task = Task(id="T", workspace_id="ws", instruction="do it")
    return ExecutionGraphBuilder().build(
        task,
        ReadPlan(purpose="p"),
        WorkspaceManifest(workspace_id="ws"),
        verify_loops=2,
        steps=steps,
    )


def test_no_steps_leaves_the_graph_shape_unchanged() -> None:
    """The default path must not grow subtasks or change dependencies."""
    graph = _graph()

    assert [node.type.value for node in graph.nodes] == [
        "read", "sandbox", "plan", "agent", "evaluation",
        "agent", "evaluation", "approval", "apply",
    ]
    assert [node for node in graph.nodes if node.type is NodeType.SUBTASK] == []


def test_steps_become_independent_subtask_nodes() -> None:
    graph = _graph(STEPS)
    subtasks = [node for node in graph.nodes if node.type is NodeType.SUBTASK]

    assert len(subtasks) == len(STEPS)
    # Independent by design: this is what lets the scheduler parallelise them.
    for node in subtasks:
        assert node.dependencies == ("T:plan",)
    assert [node.metadata["description"] for node in subtasks] == [
        "读现有实现",
        "改代码",
    ]
    assert subtasks[0].metadata["kind"] == "analysis"
    assert subtasks[0].metadata["target_paths"] == ["src/a.py"]


def test_first_agent_waits_for_every_subtask() -> None:
    graph = _graph(STEPS)
    agents = [node for node in graph.nodes if node.type is NodeType.AGENT]

    assert agents[0].dependencies == ("T:subtask:0", "T:subtask:1")
    # Later rounds still chain off the previous evaluation.
    assert agents[1].dependencies == ("T:evaluation",)


def test_subtasks_precede_agents_in_declaration_order() -> None:
    """The Temporal workflow runs nodes positionally and ignores dependencies.

    It therefore relies on SUBTASK nodes being declared before every AGENT.
    Tying the invariant to a test keeps a future reorder from silently making
    the Temporal path run the edit round before its investigation.
    """
    graph = _graph(STEPS)
    types = [node.type for node in graph.nodes]

    last_subtask = max(i for i, t in enumerate(types) if t is NodeType.SUBTASK)
    first_agent = min(i for i, t in enumerate(types) if t is NodeType.AGENT)
    first_plan = min(i for i, t in enumerate(types) if t is NodeType.PLAN)

    assert first_plan < last_subtask < first_agent


def test_temporal_codec_round_trips_subtasks() -> None:
    """The wire format must survive encoding, since Temporal sends it as JSON."""
    from Sprout.orchestration.temporal_codec import graph_to_dict, node_from_dict

    graph = _graph(STEPS)
    encoded = graph_to_dict(graph)
    decoded = [node_from_dict(node) for node in encoded["nodes"]]

    assert [node.type for node in decoded] == [node.type for node in graph.nodes]
    assert [tuple(node.dependencies) for node in decoded] == [
        tuple(node.dependencies) for node in graph.nodes
    ]


def test_malformed_steps_are_skipped_not_fatal() -> None:
    """Steps arrive as plain data from task metadata; one bad entry is survivable."""
    graph = _graph(
        (
            {"description": "  "},          # blank description
            "not-a-mapping",               # wrong type
            {"kind": "edit"},              # missing description
            {"description": "keep me", "kind": "edit"},
        )
    )
    subtasks = [node for node in graph.nodes if node.type is NodeType.SUBTASK]

    assert len(subtasks) == 1
    assert subtasks[0].metadata["description"] == "keep me"
    # Indices are positional, so the surviving node keeps its original slot.
    assert subtasks[0].id == "T:subtask:3"


@pytest.mark.asyncio
async def test_subtask_findings_reach_the_agent_prompt(tmp_path, monkeypatch) -> None:
    from Sprout.execution import file_broker
    from Sprout.execution.file_snapshot import read_fd
    from Sprout.gateway.identity import Principal
    from Sprout.llm.messages import LLMResponse
    from Sprout.orchestration.models import ExecutionGraph, ExecutionNode
    from Sprout.orchestration.service import SequentialOrchestrator
    from Sprout.session.models import Session
    from Sprout.storage.bundle import StorageBundle
    from Sprout.storage.local.sqlite.metadata import open_metadata_store
    from Sprout.task.models import DelegationScope, TaskStatus
    from Sprout.tests.conftest import ScriptedModel, build_runtime
    from Sprout.workspace.models import ResourceRef

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "users.py").write_text("def list_users():\n    return []\n")
    # Controlled secure-open adapter, as in B2 capture tests. Runtime/Policy,
    # actual file bytes/identity, Guard and source persistence remain real.
    # This unit test does not claim native Windows open-boundary coverage.
    def capture_boundary(_root, target, operation, authorize, **kwargs):
        assert operation == "snapshot"
        authorize()
        with target.open("rb") as stream:
            return read_fd(stream.fileno(), kwargs["content"])

    monkeypatch.setattr(file_broker, "file_operation", capture_boundary)
    storage = StorageBundle.in_memory()
    storage.metadata = open_metadata_store(tmp_path / "sources.db")
    model = ScriptedModel([LLMResponse(content=text) for text in (
        "分析现有用户接口", "只有 list_users()", "没有 test_user.py", "收到两个子任务结果")])
    messages = []
    chat = model.chat

    async def observed(history, **kwargs):
        messages.append(history)
        return await chat(history, **kwargs)

    model.chat = observed
    runtime, _ = build_runtime(storage=storage, model=model)
    workspace = await runtime.open_workspace(str(root))
    principal = Principal("fixture-owner", source="cli", authenticated=True)
    session = Session(id=str(uuid4()), user_id=principal.user_id, metadata={"entry_principal_v1": {
        "user_id": principal.user_id, "source": "cli", "authenticated": True}})
    await runtime._session_store.save_session(session)
    task = Task(workspace_id=workspace.id, instruction="加导出 CSV", actor=principal,
        source="cli", metadata={"session_id": session.id}, delegation_scope=DelegationScope(
            allowed_actions=frozenset({"file.read", "tool.invoke"}),
            allowed_paths=(str(root / "users.py"),)))
    await storage.metadata.save_task(task)
    read = ExecutionNode(task_id=task.id, type=NodeType.READ)
    plan = ExecutionNode(task_id=task.id, type=NodeType.PLAN, dependencies=(read.id,))
    subtasks = tuple(ExecutionNode(task_id=task.id, type=NodeType.SUBTASK,
        dependencies=(plan.id,), metadata={"index": index, "kind": kind,
            "description": description}) for index, kind, description in (
                (0, "analysis", "读实现"), (1, "test", "补测试")))
    agent = ExecutionNode(task_id=task.id, type=NodeType.AGENT,
                          dependencies=tuple(n.id for n in subtasks))
    graph = ExecutionGraph(task_id=task.id, nodes=(read, plan, *subtasks, agent))
    read_plan = ReadPlan(task_id=task.id,
                        resources=(ResourceRef(workspace.id, "users.py"),))
    executor = runtime._executor(storage.metadata)

    async def execute(node):
        current = await storage.metadata.get_task(task.id)
        return await executor.execute_node(node, workspace, read_plan, current)

    try:
        result = await SequentialOrchestrator(storage.metadata).execute(graph, handler=execute)
        assert result.status is TaskStatus.COMPLETED
        await runtime._persist_node_products()
        nodes = [await storage.metadata.get_execution_node(n.id) for n in reversed(subtasks)]
        findings = NodeExecutor._subtask_findings(nodes)
        assert [item["index"] for item in findings] == [0, 1]
        for node in nodes:
            source = await storage.metadata.get_node_result_source(node.id)
            assert source is not None
            assert source.owner == node.metadata["execution_guard"]["owner"]
        prompt = str(messages[-1])
        assert "只有 list_users()" in prompt
        assert "没有 test_user.py" in prompt
        assert "Findings from the plan" not in NodeExecutor._agent_prompt(task, workspace, ())
    finally:
        await runtime.stop()
        storage.metadata.close()


def test_subtask_findings_ignore_incomplete_nodes() -> None:
    from Sprout.orchestration.models import ExecutionNode, NodeStatus

    pending = ExecutionNode(id="T:subtask:0", task_id="T", type=NodeType.SUBTASK)
    empty = ExecutionNode(
        id="T:subtask:1",
        task_id="T",
        type=NodeType.SUBTASK,
        status=NodeStatus.COMPLETED,
        metadata={"output": {"index": 1, "kind": "other", "findings": ""}},
    )

    assert NodeExecutor._subtask_findings([pending, empty]) == ()
