"""Opt-in real-server tests. Never connect to the application's default server."""

import asyncio
import copy
import os
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.worker import Replayer, Worker
from temporalio.workflow import NondeterminismError

from Sprout.config.loader import default_settings
from Sprout.orchestration.temporal_workflows import (
    SproutExecutionWorkflow,
    run_sprout_node,
    set_activity_runtime,
)
from Sprout.runtime.factory import create_runtime


def _isolated_settings(tmp_path):
    settings = default_settings()
    settings.model.provider = "echo"
    settings.skills_dir = str(tmp_path / "skills")
    for slot in ("operational", "metadata", "session"):
        setattr(settings.storage, slot, f"sqlite:///{(tmp_path / (slot + '.db')).as_posix()}")
    settings.storage.knowledge = "memory://"
    settings.storage.observations.enabled = False
    settings.storage.trajectory_dir = str(tmp_path / "trajectory")
    settings.storage.blobs_dir = str(tmp_path / "blobs")
    settings.storage.project_root = str(tmp_path / "projects")
    settings.security.audit.path = str(tmp_path / "audit.jsonl")
    return settings


def _validation_container(action):
    """Fail closed before touching Docker: exact name plus Compose ownership."""
    name = "sprout-temporal-validation-temporal-1"
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{ index .Config.Labels "com.docker.compose.project" }}',
            name,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.stdout.strip() == "sprout-temporal-validation"
    subprocess.run(["docker", action, name], check=True, capture_output=True, timeout=30)


async def _wait_for_server(host, namespace):
    deadline = asyncio.get_running_loop().time() + 45
    while True:
        try:
            return await asyncio.wait_for(Client.connect(host, namespace=namespace), timeout=3)
        except Exception as exc:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Isolated Temporal server did not recover") from exc
            await asyncio.sleep(1)


async def test_real_unassembled_coding_worker_waits_without_node_effects(tmp_path, monkeypatch):
    """Exercise the activity boundary, not a mock dispatcher or local handler.

    The deliberately incomplete plan is rejection input, never execution proof.
    Submit the fixed graph through the real backend to reach worker admission
    independently of the client's earlier admission check.
    """
    from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeType
    from Sprout.orchestration.terminal.temporal import TemporalOrchestratorBackend
    from Sprout.task.models import Task, TaskStatus

    host = os.getenv("SPROUT_TEMPORAL_TEST_HOST")
    if not host:
        pytest.skip("Set SPROUT_TEMPORAL_TEST_HOST for isolated integration tests")
    assert host == "127.0.0.1:17233"
    namespace, queue = "sprout-validation-" + uuid4().hex, "validation-" + uuid4().hex
    admin = await asyncio.wait_for(Client.connect(host), timeout=15)
    await admin.workflow_service.register_namespace(RegisterNamespaceRequest(
        namespace=namespace, workflow_execution_retention_period=Duration(seconds=86400)),
        timeout=timedelta(seconds=10))
    client = await asyncio.wait_for(Client.connect(host, namespace=namespace), timeout=15)
    for key, value in {"TEMPORAL_HOST": host, "TEMPORAL_NAMESPACE": namespace,
                       "TEMPORAL_TASK_QUEUE": queue}.items():
        monkeypatch.setenv(key, value)
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True, timeout=15)
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    runtime = create_runtime(_isolated_settings(tmp_path))
    await runtime.start()
    set_activity_runtime(runtime)
    try:
        workspace = await runtime.open_workspace(root)
        task = Task(workspace_id=workspace.id, instruction="Fixed admission rejection fixture",
                    status=TaskStatus.RUNNING,
                    metadata={"execution_mode": "code", "replay_plan": None})
        metadata = runtime.storage.metadata
        await metadata.save_task(task)
        agent = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
        apply = ExecutionNode(task_id=task.id, type=NodeType.APPLY, dependencies=(agent.id,))
        graph = ExecutionGraph(task_id=task.id, nodes=(agent, apply), entry_node_ids=(agent.id,))
        for node in graph.nodes:
            await metadata.save_execution_node(node)
        before = await metadata.list_execution_nodes(task.id)
        backend = TemporalOrchestratorBackend(metadata=metadata)
        async with Worker(client, task_queue=queue, workflows=[SproutExecutionWorkflow],
                          activities=[run_sprout_node]):
            result = await asyncio.wait_for(backend.execute(graph), timeout=30)
        assert result.status == TaskStatus.WAITING_RESOURCE
        assert await metadata.list_execution_nodes(task.id) == before
        assert not await runtime.list_change_proposals(task.id)
        records = await metadata.list_task_submissions(task.id)
        assert len(records) == 1 and records[0]["accepted"]
        assert records[0]["result"]["waiting_reason"] == "isolated_worker_not_assembled"
        history = await client.get_workflow_handle(records[0]["id"]).fetch_history(
            rpc_timeout=timedelta(seconds=10))
        (tmp_path / "history.json").write_text(history.to_json(), encoding="utf-8")
        assert sum(event.HasField("activity_task_scheduled_event_attributes")
                   for event in history.events) == 1  # APPLY never scheduled.
        replay = await asyncio.wait_for(
            Replayer(workflows=[SproutExecutionWorkflow]).replay_workflow(history), timeout=20)
        assert replay.replay_failure is None
        set_activity_runtime(None)
        await runtime.stop()
        runtime = create_runtime(_isolated_settings(tmp_path))
        await runtime.start()
        metadata = runtime.storage.metadata
        # No Worker is running: returning the known waiting result must use the
        # original durable submission rather than dispatching another workflow.
        repeated = await asyncio.wait_for(
            TemporalOrchestratorBackend(metadata=metadata).execute(graph), timeout=15)
        assert repeated.status == TaskStatus.WAITING_RESOURCE
        assert await metadata.list_task_submissions(task.id) == records
        assert await metadata.list_execution_nodes(task.id) == before
        assert not await runtime.list_change_proposals(task.id)
        assert (root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    finally:
        set_activity_runtime(None)
        await runtime.stop()


@pytest.mark.parametrize(
    "mode",
    [
        "replay",
        "restart-terminal",
        "server-outage",
        "worker-kill",
        "ack-lost-open",
        "ack-lost-closed",
        "server-active-outage",
    ],
)
async def test_real_runtime_submission_and_history_replay(tmp_path, monkeypatch, mode):
    outage = mode == "server-outage"
    crash = mode == "worker-kill"
    active_outage = mode == "server-active-outage"
    host = os.getenv("SPROUT_TEMPORAL_TEST_HOST")
    if not host:
        pytest.skip("Set SPROUT_TEMPORAL_TEST_HOST for isolated integration tests")
    assert host == "127.0.0.1:17233", "Only the isolated validation endpoint is permitted"
    if (outage or active_outage) and os.getenv("SPROUT_TEMPORAL_DOCKER_FAULTS") != "1":
        pytest.skip("Docker fault injection requires explicit opt-in")
    namespace = "sprout-validation-" + uuid4().hex
    queue = "validation-" + uuid4().hex
    admin = await asyncio.wait_for(Client.connect(host), timeout=15)
    await admin.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=namespace, workflow_execution_retention_period=Duration(seconds=86400)
        ),
        timeout=timedelta(seconds=10),
    )
    client = await asyncio.wait_for(Client.connect(host, namespace=namespace), timeout=15)
    for key, value in {
        "TEMPORAL_HOST": host,
        "TEMPORAL_NAMESPACE": namespace,
        "TEMPORAL_TASK_QUEUE": queue,
    }.items():
        monkeypatch.setenv(key, value)
    runtime = create_runtime(_isolated_settings(tmp_path))
    await runtime.start()
    set_activity_runtime(runtime)
    child = None
    execution = None
    acknowledged_run = None
    start_attempts = []
    try:
        root = tmp_path / "repo"
        root.mkdir()
        (root / "app.py").write_text("value = 1\n", encoding="utf-8")
        workspace = await runtime.open_workspace(root)
        task = await runtime.create_task_planned(
            workspace.id, "Explain app.py without changing files", execution_mode="read_only"
        )
        pending_id = None
        if active_outage:
            from Sprout.runtime.nodes import NodeExecutor

            original_execute = NodeExecutor.execute_node
            reached = asyncio.Event()
            release = asyncio.Event()

            async def pause_after_read(self, node, *args, **kwargs):
                output = await original_execute(self, node, *args, **kwargs)
                if node.type.value == "read":
                    reached.set()
                    await release.wait()
                return output

            monkeypatch.setattr(NodeExecutor, "execute_node", pause_after_read)
        if mode.startswith("ack-lost"):
            start_workflow = Client.start_workflow

            async def lose_first_confirmation(self, *args, **kwargs):
                start_attempts.append(kwargs["id"])
                handle = await start_workflow(self, *args, **kwargs)
                nonlocal acknowledged_run
                acknowledged_run = handle.first_execution_run_id
                if mode == "ack-lost-closed":
                    assert (await asyncio.wait_for(handle.result(), timeout=20))[
                        "status"
                    ] == "completed"
                # The real server accepted the request. Drop only the caller's
                # success acknowledgement, not the request or server history.
                raise TimeoutError("Injected loss of real start acknowledgement")

            monkeypatch.setattr(Client, "start_workflow", lose_first_confirmation)

            async def submit_without_confirmation():
                uncertain = await runtime.execute(task)
                assert uncertain.status == "waiting_resource"
                records = await runtime.storage.metadata.list_task_submissions(task.id)
                assert len(records) == 1 and records[0]["state"] == "uncertain"
                assert records[0]["phase"] == "start" and not records[0].get("accepted")
                assert acknowledged_run
                return records[0]["id"]

            if mode == "ack-lost-closed":
                async with Worker(
                    client,
                    task_queue=queue,
                    workflows=[SproutExecutionWorkflow],
                    activities=[run_sprout_node],
                ):
                    pending_id = await submit_without_confirmation()
            else:
                pending_id = await submit_without_confirmation()
                nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
                assert all(node.attempts == 0 for node in nodes)
            # Restart the client-side Runtime and reopen the persisted stores:
            # recovery must not rely on an in-memory workflow handle.
            await runtime.stop()
            runtime = create_runtime(_isolated_settings(tmp_path))
            await runtime.start()
            set_activity_runtime(runtime)
            task = await runtime.storage.metadata.get_task(task.id)
        if crash:
            from Sprout.orchestration.terminal import temporal

            encode = temporal.graph_to_dict

            def short_activity_timeout(graph):
                payload = encode(graph)
                for node in payload["nodes"]:
                    node["budget"]["timeout_seconds"] = 5
                return payload

            monkeypatch.setattr(temporal, "graph_to_dict", short_activity_timeout)
            child = subprocess.Popen(
                [sys.executable, "-m", "Sprout.tests.temporal_worker_fault_helper", str(tmp_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            execution = asyncio.create_task(runtime.execute(task))
            deadline = asyncio.get_running_loop().time() + 20
            while not (tmp_path / "effect.log").exists():
                assert child.poll() is None, "Fault worker exited before executing node"
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("Fault worker never reached interrupt boundary")
                await asyncio.sleep(0.1)
            child.kill()
            await asyncio.to_thread(child.wait, timeout=10)
            nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
            assert nodes[0].metadata["execution_guard"]["state"] == "active"
        if outage:
            try:
                await asyncio.to_thread(_validation_container, "stop")
                unavailable = await asyncio.wait_for(runtime.execute(task), timeout=20)
                assert unavailable.status == "waiting_resource"
                pending = await runtime.storage.metadata.list_task_submissions(task.id)
                assert len(pending) == 1 and pending[0]["state"] == "pending"
                pending_id = pending[0]["id"]
                assert not pending[0].get("accepted")
                nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
                assert all(node.attempts == 0 for node in nodes), "Local execution fallback"
                assert (root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
            finally:
                await asyncio.to_thread(_validation_container, "start")
                client = await _wait_for_server(host, namespace)
        async with Worker(
            client,
            task_queue=queue,
            workflows=[SproutExecutionWorkflow],
            activities=[run_sprout_node],
        ):
            if active_outage:
                execution = asyncio.create_task(runtime.execute(task))
                await asyncio.wait_for(reached.wait(), timeout=15)
                try:
                    await asyncio.to_thread(_validation_container, "stop")
                    release.set()
                    deadline = asyncio.get_running_loop().time() + 10
                    while True:
                        nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
                        settled = next(node for node in nodes if node.type.value == "read")
                        if settled.metadata.get("execution_guard", {}).get("state") == "completed":
                            assert settled.attempts == 1
                            break
                        if asyncio.get_running_loop().time() >= deadline:
                            raise TimeoutError("Read result was not durably saved during outage")
                        await asyncio.sleep(0.1)
                    records = await runtime.storage.metadata.list_task_submissions(task.id)
                    assert len(records) == 1 and records[0]["accepted"]
                    pending_id = records[0]["id"]
                finally:
                    release.set()
                    await asyncio.to_thread(_validation_container, "start")
                    await _wait_for_server(host, namespace)
            result = await asyncio.wait_for(execution or runtime.execute(task), timeout=45)
            if crash:
                assert result.status == "waiting_resource"
                nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
                assert sum(node.attempts for node in nodes) == 1
                assert (tmp_path / "effect.log").read_text(encoding="utf-8") == "executed\n"
                assert not await runtime.list_change_proposals(task.id)
                records = await runtime.storage.metadata.list_task_submissions(task.id)
                assert len(records) == 1
                history = await client.get_workflow_handle(records[0]["id"]).fetch_history()
                (tmp_path / "history.json").write_text(history.to_json(), encoding="utf-8")
                # Temporal may omit intermediate retry timeouts from history;
                # the final started attempt is authoritative retry evidence.
                assert any(
                    event.activity_task_started_event_attributes.attempt >= 2
                    for event in history.events
                    if event.HasField("activity_task_started_event_attributes")
                )
                replay = await Replayer(workflows=[SproutExecutionWorkflow]).replay_workflow(
                    history
                )
                assert replay.replay_failure is None
                retried = await runtime.execute(task)
                assert retried.status == "waiting_resource"
                nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
                assert sum(node.attempts for node in nodes) == 1
                assert (tmp_path / "effect.log").read_text(encoding="utf-8") == "executed\n"
                assert (root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
                return
            assert result.status == "completed"
            await runtime.execute(task)
            records = await runtime.storage.metadata.list_task_submissions(task.id)
            assert len(records) == 1
            nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
            assert nodes and all(node.attempts == 1 for node in nodes)
            if mode == "restart-terminal":
                # Real Runtime and server history, no activity/dispatcher mock.
                # Reopen persistence and submit a deliberately stale caller
                # snapshot: terminal admission must precede workspace lookup.
                from Sprout.task.models import TaskStatus

                before_nodes = copy.deepcopy(nodes)
                before_records = copy.deepcopy(records)
                stale = replace(task, workspace_id="nonexistent-stale-workspace",
                                instruction="stale instruction", status=TaskStatus.WAITING_APPROVAL)
                set_activity_runtime(None)
                await runtime.stop()
                runtime = create_runtime(_isolated_settings(tmp_path))
                await runtime.start()
                set_activity_runtime(runtime)
                resumed = await asyncio.wait_for(runtime.resume_task(stale), timeout=10)
                assert resumed.status == TaskStatus.COMPLETED
                assert await runtime.storage.metadata.list_execution_nodes(task.id) == before_nodes
                after_records = await runtime.storage.metadata.list_task_submissions(task.id)
                assert after_records == before_records
                current = await runtime.storage.metadata.get_task(task.id)
                assert current.status == TaskStatus.COMPLETED
            record = records[0]
            if pending_id:
                assert record["id"] == pending_id
            if acknowledged_run:
                assert record["run_id"] == acknowledged_run
                assert start_attempts == [pending_id, pending_id]
            history = await client.get_workflow_handle(
                record["id"], run_id=record.get("run_id")
            ).fetch_history(rpc_timeout=timedelta(seconds=10))
            (tmp_path / "history.json").write_text(history.to_json(), encoding="utf-8")
            replay = await asyncio.wait_for(
                Replayer(workflows=[SproutExecutionWorkflow]).replay_workflow(history), timeout=20
            )
            assert replay.replay_failure is None
            # Alter a copy only: one extra activity must disagree with the real
            # completed history. This checks that replay isn't a vacuous pass.
            incompatible = copy.deepcopy(history)
            started = incompatible.events[0].workflow_execution_started_event_attributes
            graph = (await client.data_converter.decode(started.input.payloads))[0]
            extra = copy.deepcopy(graph["nodes"][-1])
            extra["id"] += ":incompatible-extra"
            graph["nodes"].append(extra)
            payloads = await client.data_converter.encode([graph])
            del started.input.payloads[:]
            started.input.payloads.extend(payloads)
            with pytest.raises(NondeterminismError):
                await asyncio.wait_for(
                    Replayer(workflows=[SproutExecutionWorkflow]).replay_workflow(incompatible),
                    timeout=20,
                )
            assert (root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            await asyncio.to_thread(child.wait, timeout=10)
        if execution is not None and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        set_activity_runtime(None)
        await runtime.stop()
