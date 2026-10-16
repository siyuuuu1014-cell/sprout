"""Durable activity ownership must survive retries and lost acknowledgements."""

import asyncio
from dataclasses import replace

import pytest

from Sprout.orchestration.execution_guard import NodeExecutionGuard
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task


async def _fixture(path):
    store = open_metadata_store(str(path))
    task = Task(workspace_id="fixture", metadata={"execution_mode": "read_only"})
    await store.save_task(task)
    return store, ExecutionNode(task_id=task.id, type=NodeType.AGENT)


async def test_only_one_durable_owner_can_claim_a_node(tmp_path):
    store, node = await _fixture(tmp_path / "control.db")
    try:
        results = await asyncio.gather(
            store.claim_execution_node(node, "owner-a", ""),
            store.claim_execution_node(node, "owner-b", ""),
        )
        assert sorted(results) == [False, True]
        saved = await store.get_execution_node(node.id)
        assert saved.status == NodeStatus.RUNNING
        assert saved.attempts == 1
    finally:
        store.close()


async def test_reopen_never_steals_an_unconfirmed_claim(tmp_path):
    path = tmp_path / "control.db"
    store, node = await _fixture(path)
    assert await store.claim_execution_node(node, "crashed-owner", "")
    store.close()
    store = open_metadata_store(str(path))
    try:
        assert not await store.claim_execution_node(node, "new-owner", "new-decision")
        assert (await store.get_execution_node(node.id)).attempts == 1
    finally:
        store.close()


async def test_foreign_owner_cannot_publish_completion(tmp_path):
    store, node = await _fixture(tmp_path / "control.db")
    try:
        assert await store.claim_execution_node(node, "owner", "")
        completed = replace(node, status=NodeStatus.COMPLETED)
        assert not await store.finish_execution_node(completed, "intruder", "completed")
        assert (await store.get_execution_node(node.id)).status == NodeStatus.RUNNING
    finally:
        store.close()


async def test_concurrent_retry_never_invokes_a_second_executor(tmp_path):
    store, node = await _fixture(tmp_path / "control.db")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def execute(running):
        calls.append(running.id)
        entered.set()
        await release.wait()
        return {"value": "confirmed"}

    first = asyncio.create_task(NodeExecutionGuard(store).execute(node, execute))
    try:
        await entered.wait()
        duplicate = await NodeExecutionGuard(store).execute(node, execute)
        assert duplicate["output"]["waiting_kind"] == "resource"
        assert calls == [node.id]
        release.set()
        result = await first
        assert result["status"] == "completed"
        repeated = await NodeExecutionGuard(store).execute(node, execute)
        assert repeated["output"] == {"value": "confirmed"}
        assert len(calls) == 1
    finally:
        release.set()
        await first
        store.close()


async def test_error_after_side_effect_is_uncertain_not_replayed(tmp_path):
    store, node = await _fixture(tmp_path / "control.db")
    calls = []

    async def execute(running):
        calls.append("side-effect")
        raise TimeoutError("tool acknowledgement lost")

    try:
        result = await NodeExecutionGuard(store).execute(node, execute)
        assert result["output"]["waiting_kind"] == "resource"
        saved = await store.get_execution_node(node.id)
        assert saved.status == NodeStatus.WAITING
        assert saved.metadata["execution_guard"]["state"] == "uncertain"
        await NodeExecutionGuard(store).execute(node, execute, resume_key="new-approval")
        assert calls == ["side-effect"]
        assert (await store.get_execution_node(node.id)).attempts == 1
    finally:
        store.close()


async def test_cancelled_owner_leaves_non_expiring_uncertainty(tmp_path):
    store, node = await _fixture(tmp_path / "control.db")
    entered = asyncio.Event()

    async def execute(running):
        entered.set()
        await asyncio.Event().wait()

    owner = asyncio.create_task(NodeExecutionGuard(store).execute(node, execute))
    try:
        await entered.wait()
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        result = await NodeExecutionGuard(store).execute(node, execute)
        assert result["output"]["reason"] == "execution_result_unconfirmed"
        assert (await store.get_execution_node(node.id)).attempts == 1
    finally:
        if not owner.done():
            owner.cancel()
        store.close()


async def test_output_commit_failure_never_replays_effect(tmp_path, monkeypatch):
    store, node = await _fixture(tmp_path / "control.db")
    calls = []

    async def execute(running):
        calls.append("effect")
        return {"value": "not-committed"}

    async def fail_commit(*args, **kwargs):
        raise OSError("database unavailable after effect")

    monkeypatch.setattr(store, "finish_execution_node", fail_commit)
    try:
        with pytest.raises(OSError):
            await NodeExecutionGuard(store).execute(node, execute)
        result = await NodeExecutionGuard(store).execute(node, execute)
        assert result["output"]["waiting_kind"] == "resource"
        assert calls == ["effect"]
    finally:
        store.close()


async def test_known_approval_wait_requires_a_new_decision(tmp_path):
    store, node = await _fixture(tmp_path / "control.db")
    calls = []

    async def execute(running):
        calls.append(running.attempts)
        return {"waiting": True, "approval_id": "approval"}

    try:
        await NodeExecutionGuard(store).execute(node, execute)
        await NodeExecutionGuard(store).execute(node, execute)
        assert calls == [1]
        await NodeExecutionGuard(store).execute(node, execute, resume_key="decision")
        await NodeExecutionGuard(store).execute(node, execute, resume_key="decision")
        assert calls == [1, 2]
    finally:
        store.close()


@pytest.mark.parametrize("status", [NodeStatus.RUNNING, NodeStatus.FAILED])
async def test_legacy_unconfirmed_nodes_are_not_replayed(tmp_path, status):
    store, node = await _fixture(tmp_path / "control.db")
    await store.save_execution_node(replace(node, status=status))

    async def execute(running):
        pytest.fail("Historical unconfirmed execution must not be replayed")

    try:
        result = await NodeExecutionGuard(store).execute(node, execute)
        assert result["output"]["waiting_kind"] == "resource"
    finally:
        store.close()


async def test_general_save_cannot_remove_execution_ownership(tmp_path):
    store, node = await _fixture(tmp_path / "control.db")
    try:
        assert await store.claim_execution_node(node, "owner", "")
        with pytest.raises(ValueError, match="owner-checked"):
            await store.save_execution_node(node)
        assert (await store.get_execution_node(node.id)).metadata["execution_guard"][
            "owner"
        ] == "owner"
    finally:
        store.close()


async def test_local_scheduler_cannot_bypass_external_ownership(tmp_path):
    from Sprout.orchestration.models import ExecutionGraph
    from Sprout.orchestration.service import SequentialOrchestrator
    from Sprout.task.models import TaskStatus

    store, node = await _fixture(tmp_path / "control.db")
    assert await store.claim_execution_node(node, "owner", "")

    async def handler(running):
        pytest.fail("Switching schedulers must not bypass ownership")

    try:
        # The omitted protected node is still authoritative for this task.
        result = await SequentialOrchestrator(store).execute(
            ExecutionGraph(task_id=node.task_id, nodes=()),
            handler=handler,
        )
        assert result.status == TaskStatus.WAITING_RESOURCE
        assert (await store.get_task(node.task_id)).status == TaskStatus.WAITING_RESOURCE
    finally:
        store.close()


async def test_independent_processes_share_atomic_node_ownership(tmp_path):
    import sys

    path = tmp_path / "control.db"
    store, node = await _fixture(path)
    store.close()
    code = """
import asyncio, sys
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.orchestration.models import ExecutionNode, NodeType
async def run():
    store = open_metadata_store(sys.argv[1])
    try:
        node = ExecutionNode(id=sys.argv[2], task_id=sys.argv[3], type=NodeType.AGENT)
        print(int(await store.claim_execution_node(node, sys.argv[4], '')))
    finally:
        store.close()
asyncio.run(run())
"""
    processes = []
    try:
        for owner in ("process-a", "process-b"):
            processes.append(
                await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    code,
                    str(path),
                    node.id,
                    node.task_id,
                    owner,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            )
        results = await asyncio.wait_for(
            asyncio.gather(*(process.communicate() for process in processes)),
            timeout=30,
        )
        assert all(process.returncode == 0 for process in processes), results
        assert sorted(output.strip() for output, error in results) == [b"0", b"1"]
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def _runtime_fixture(store, tmp_path, executor):
    from types import SimpleNamespace

    from Sprout.runtime.runtime import Runtime
    from Sprout.workspace.models import Workspace

    class Operational:
        async def list_approvals(self, status=None):
            return []

    await store.save_workspace(Workspace(id="fixture", root=tmp_path))
    runtime = Runtime.__new__(Runtime)
    runtime.storage = SimpleNamespace(metadata=store, operational=Operational())
    runtime._scanner = SimpleNamespace(
        scan=lambda workspace: None,
        build_read_plan=lambda workspace, task: None,
    )
    runtime._executor = lambda metadata: executor
    runtime._trajectory_dir = tmp_path / "trajectory"
    return runtime


async def test_runtime_claims_before_executor_and_keeps_read_only_admission(tmp_path):
    store, node = await _fixture(tmp_path / "control.db")
    calls = []

    class Executor:
        async def execute_node(self, running, workspace, read_plan, task):
            saved = await store.get_execution_node(running.id)
            assert saved.status == NodeStatus.RUNNING
            assert saved.metadata["execution_guard"]["state"] == "active"
            assert running.metadata["read_only"] is True
            calls.append(running.id)
            return {"text": "confirmed"}

    runtime = await _runtime_fixture(store, tmp_path, Executor())
    try:
        result = await runtime.execute_node(node)
        assert result["status"] == "completed"
        assert (await runtime.execute_node(node))["output"] == {"text": "confirmed"}
        assert calls == [node.id]
        saved = await store.get_execution_node(node.id)
        assert saved.metadata["execution_guard"]["state"] == "completed"
        report = await runtime.get_task_execution_report(node.task_id)
        assert report["nodes"][0]["execution_state"] == "completed"
    finally:
        store.close()


async def test_cancelled_task_blocks_new_activity_before_preparation(tmp_path):
    from Sprout.task.models import TaskStatus

    store, node = await _fixture(tmp_path / "control.db")
    await store.update_task_status(node.task_id, TaskStatus.CANCELLED)
    runtime = await _runtime_fixture(store, tmp_path, None)
    try:
        result = await runtime.execute_node(node)
        assert result["status"] == "cancelled"
        assert await store.get_execution_node(node.id) is None
    finally:
        store.close()


async def test_runtime_unknown_claim_is_inspectable_without_reset(tmp_path):
    store, node = await _fixture(tmp_path / "control.db")
    assert await store.claim_execution_node(node, "interrupted", "")
    runtime = await _runtime_fixture(store, tmp_path, None)
    try:
        result = await runtime.execute_node(node)
        assert result["output"]["waiting_kind"] == "resource"
        report = await runtime.get_task_execution_report(node.task_id)
        assert report["nodes"][0]["execution_state"] == "occupied_or_unconfirmed"
        assert (await store.get_execution_node(node.id)).attempts == 1
        with pytest.raises(LookupError):
            await runtime.get_task_execution_report("missing")
    finally:
        store.close()


async def test_workflow_preserves_resource_wait_not_approval_or_completed(monkeypatch):
    from Sprout.orchestration.temporal_workflows import SproutExecutionWorkflow

    calls = []

    async def activity(function, node, **kwargs):
        calls.append(node["id"])
        return {
            "node_id": node["id"],
            "status": "waiting",
            "output": {
                "waiting": True,
                "waiting_kind": "resource",
            },
        }

    monkeypatch.setattr("temporalio.workflow.execute_activity", activity)
    monkeypatch.setattr("temporalio.workflow.patched", lambda _: True)
    result = await SproutExecutionWorkflow().run(
        {
            "nodes": [
                {"id": "uncertain", "type": "agent", "status": "pending"},
                {"id": "apply", "type": "apply", "status": "pending"},
            ]
        }
    )
    assert result["status"] == "waiting_resource"
    assert result["node_id"] == "uncertain"
    assert calls == ["uncertain"]


async def test_known_completion_can_resume_a_closed_resource_wait(tmp_path, monkeypatch):
    from Sprout.orchestration.models import ExecutionGraph
    from Sprout.orchestration.terminal.temporal import TemporalOrchestratorBackend
    from Sprout.task.models import TaskStatus

    store, node = await _fixture(tmp_path / "control.db")
    assert await store.claim_execution_node(node, "owner", "")
    runtime = await _runtime_fixture(store, tmp_path, None)

    class Client:
        ids = []

        async def start_workflow(self, *args, **kwargs):
            self.ids.append(kwargs["id"])
            return self

        async def result(self):
            return {
                "status": "waiting_resource" if len(self.ids) == 1 else "completed",
                "node_id": node.id,
            }

    client = Client()

    async def connect(*args, **kwargs):
        return client

    monkeypatch.setattr("temporalio.client.Client.connect", connect)
    graph = ExecutionGraph(task_id=node.task_id, nodes=(node,))
    backend = TemporalOrchestratorBackend(metadata=store)
    try:
        restored = await runtime._restore_graph(graph)
        assert (await backend.execute(restored)).status == TaskStatus.WAITING_RESOURCE
        assert (
            await backend.execute(await runtime._restore_graph(graph))
        ).status == TaskStatus.WAITING_RESOURCE
        assert len(client.ids) == 1
        running = await store.get_execution_node(node.id)
        assert await store.finish_execution_node(
            replace(
                running,
                status=NodeStatus.COMPLETED,
                metadata={**running.metadata, "output": {"text": "known"}},
            ),
            "owner",
            "completed",
        )
        confirmed = await runtime._restore_graph(graph)
        assert confirmed.nodes[0].status == NodeStatus.COMPLETED
        assert (await backend.execute(confirmed)).status == TaskStatus.COMPLETED
        assert len(client.ids) == 2
        assert client.ids[0] != client.ids[1]
        assert (await store.get_execution_node(node.id)).attempts == 1
    finally:
        store.close()


def test_cli_execution_query_is_read_only(monkeypatch):
    from typer.testing import CliRunner

    from Sprout.cli.commands import project

    class Gateway:
        async def task_execution_report(self, task_id):
            assert task_id == "known"
            return {"nodes": [{"execution_state": "uncertain"}]}

    monkeypatch.setattr(project, "_gateway", lambda config: Gateway())
    result = CliRunner().invoke(project.app, ["task-executions", "known"])
    assert result.exit_code == 0, result.output
    assert '"execution_state": "uncertain"' in result.output
