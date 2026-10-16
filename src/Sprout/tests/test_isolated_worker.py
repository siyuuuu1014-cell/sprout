from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.exceptions import ApplicationError

from Sprout.evolution.isolated_replay import isolated_settings
from Sprout.orchestration.isolated_worker import (
    COMMIT_ROUTING_PROFILE,
    IsolatedCodingWorker,
    IsolatedCommitWorker,
    commit_task_queue,
    experiment_task_queue,
)
from Sprout.orchestration.temporal_codec import node_to_dict
from Sprout.orchestration.terminal.temporal import TemporalConfig, TemporalOrchestratorBackend
from Sprout.tests.test_coding_assembly import fixture


async def assembled(tmp_path, monkeypatch):
    store, runtime, task, node, sandbox = await fixture(tmp_path, monkeypatch)
    directory = Path(task.metadata["replay_plan"]["payload"]["repository_root"]).parent
    runtime._settings = isolated_settings(directory, runtime._settings)
    runtime._isolated_coding_dispatch = Mock()
    profile = task.metadata["replay_plan"]["payload"]["coding_profile"]
    runtime._isolated_coding_image = profile["image"]
    runtime.execute_node = AsyncMock(return_value={"status": "completed"})
    config = TemporalConfig(host="127.0.0.1:17233", namespace="fixture",
                            task_queue=experiment_task_queue(task))
    return store, runtime, task, node, sandbox, config


async def test_task_bound_activity_does_not_use_shared_runtime(tmp_path, monkeypatch):
    store, runtime, task, node, _, config = await assembled(tmp_path, monkeypatch)
    try:
        worker = IsolatedCodingWorker(runtime, task, config)
        assert callable(runtime._isolated_coding_session_factory)
        assert await worker.execute_node(node_to_dict(node)) == {"status": "completed"}
        runtime.execute_node.assert_awaited_once_with(node)
        runtime.run_task_process.assert_not_called()
        runtime.authorize_task_process.assert_not_called()
        assert experiment_task_queue(replace(task, id="different")) != config.task_queue
        with pytest.raises(ValueError, match="already belongs"):
            IsolatedCodingWorker(runtime, task, config)
    finally:
        store.close()


async def test_commit_worker_has_separate_queue_and_no_coding_capabilities(tmp_path, monkeypatch):
    store, runtime, task, _, _, config = await assembled(tmp_path, monkeypatch)
    try:
        del runtime._isolated_coding_dispatch
        runtime.start = AsyncMock()
        coding_queue = config.task_queue
        config.task_queue = commit_task_queue(task, COMMIT_ROUTING_PROFILE)
        worker = IsolatedCommitWorker(runtime, task, config)
        assert config.task_queue != coding_queue
        assert commit_task_queue(task, None) == coding_queue
        assert not hasattr(worker, "execute_node")
        assert not hasattr(runtime, "_isolated_coding_commands")
        assert not hasattr(runtime, "_isolated_coding_session_factory")
        runtime.start.assert_not_awaited()
        runtime.execute_node.assert_not_awaited()
        with pytest.raises(ValueError, match="own Runtime"):
            IsolatedCommitWorker(runtime, task, config)
    finally:
        store.close()


async def test_commit_worker_refuses_coding_queue_without_mutating_runtime(tmp_path, monkeypatch):
    store, runtime, task, _, _, config = await assembled(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValueError, match="Dedicated commit queue"):
            IsolatedCommitWorker(runtime, task, config)
        assert not hasattr(runtime, "_isolated_worker_task_binding")
        with pytest.raises(ValueError, match="Unsupported"):
            commit_task_queue(task, "unknown")
    finally:
        store.close()


@pytest.mark.parametrize("change", ["queue", "storage", "image", "dispatcher", "mode"])
async def test_bad_assembly_cannot_install_session(tmp_path, monkeypatch, change):
    store, runtime, task, _, _, config = await assembled(tmp_path, monkeypatch)
    try:
        if change == "queue":
            config.task_queue = "sprout-tasks"
        elif change == "storage":
            runtime._settings.storage.metadata = "sqlite:///shared.db"
        elif change == "image":
            runtime._isolated_coding_image = "sha256:" + "b" * 64
        elif change == "dispatcher":
            runtime._isolated_coding_dispatch = None
        else:
            runtime._isolated_coding_required = False
        with pytest.raises(ValueError):
            IsolatedCodingWorker(runtime, task, config)
        assert not hasattr(runtime, "_isolated_coding_session_factory")
        runtime.execute_node.assert_not_called()
    finally:
        store.close()


@pytest.mark.parametrize("change", ["foreign", "task", "config", "image", "storage"])
async def test_activity_drift_rejected_before_node_execution(tmp_path, monkeypatch, change):
    store, runtime, task, node, _, config = await assembled(tmp_path, monkeypatch)
    try:
        worker = IsolatedCodingWorker(runtime, task, config)
        data = node_to_dict(node)
        if change == "foreign":
            data["task_id"] = "different"
        elif change == "task":
            await store.save_task(replace(task, instruction="changed"))
        elif change == "config":
            runtime._settings.model.model = "changed"
        elif change == "image":
            runtime._isolated_coding_image = "sha256:" + "b" * 64
        else:
            runtime._settings.storage.metadata = "sqlite:///shared.db"
        if change == "foreign":
            with pytest.raises(ApplicationError) as exc:
                await worker.execute_node(data)
            assert exc.value.non_retryable
        else:
            result = await worker.execute_node(data)
            assert result["status"] == "waiting_resource"
            assert result["output"]["waiting_reason"] == "isolated_worker_binding_changed"
        runtime.execute_node.assert_not_called()
        runtime.run_task_process.assert_not_called()
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["bound", "foreign", "drift"])
async def test_real_sdk_instance_activity_registration(tmp_path, monkeypatch, mode):
    """Real SDK/server transport; execute_node/dispatcher remain test spies."""
    import asyncio
    import os
    from datetime import timedelta
    from uuid import uuid4

    from google.protobuf.duration_pb2 import Duration
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.worker import Replayer

    from Sprout.orchestration.models import ExecutionGraph
    from Sprout.orchestration.temporal_codec import graph_to_dict
    from Sprout.orchestration.temporal_workflows import SproutExecutionWorkflow

    host = os.getenv("SPROUT_TEMPORAL_TEST_HOST")
    if not host:
        pytest.skip("Explicit isolated Temporal endpoint required")
    assert host == "127.0.0.1:17233"
    store, runtime, task, node, _, config = await assembled(tmp_path, monkeypatch)
    serving = None
    try:
        config.namespace = "sprout-validation-" + uuid4().hex
        admin = await asyncio.wait_for(Client.connect(host), timeout=15)
        await admin.workflow_service.register_namespace(RegisterNamespaceRequest(
            namespace=config.namespace,
            workflow_execution_retention_period=Duration(seconds=86400)),
            timeout=timedelta(seconds=10))
        client = await asyncio.wait_for(
            Client.connect(host, namespace=config.namespace), timeout=15)
        worker = IsolatedCodingWorker(runtime, task, config)
        if mode == "drift":
            runtime._isolated_coding_image = "sha256:" + "b" * 64
        serving = asyncio.create_task(worker.run())
        sent = replace(node, task_id="another-experiment") if mode == "foreign" else node
        handle = await client.start_workflow("SproutExecutionWorkflow",
            graph_to_dict(ExecutionGraph(task_id=task.id, nodes=(sent,))),
            id="instance-registration-" + uuid4().hex, task_queue=config.task_queue)
        result = await asyncio.wait_for(handle.result(), timeout=20)
        expected = {"foreign": "failed", "bound": "completed", "drift": "waiting_resource"}
        assert result["status"] == expected[mode]
        if mode != "bound":
            runtime.execute_node.assert_not_called()
        else:
            runtime.execute_node.assert_awaited_once_with(node)
        history = await handle.fetch_history(rpc_timeout=timedelta(seconds=10))
        (tmp_path / "history.json").write_text(history.to_json(), encoding="utf-8")
        assert sum(event.HasField("activity_task_scheduled_event_attributes")
                   for event in history.events) == 1
        if mode == "foreign":
            failures = [event.activity_task_failed_event_attributes.failure.application_failure_info
                        for event in history.events
                        if event.HasField("activity_task_failed_event_attributes")]
            assert len(failures) == 1 and failures[0].non_retryable
        elif mode == "drift":
            assert result["nodes"][0]["output"]["waiting_reason"] == (
                "isolated_worker_binding_changed")
        replay = await asyncio.wait_for(
            Replayer(workflows=[SproutExecutionWorkflow]).replay_workflow(history), timeout=20)
        assert replay.replay_failure is None
        runtime.run_task_process.assert_not_called()
        runtime.authorize_task_process.assert_not_called()
    finally:
        if serving is not None:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
        store.close()


async def test_real_runtime_read_node_on_dedicated_worker(tmp_path):
    """Real Runtime/guard/SQLite/read broker; fixed READ graph, no coding claim."""
    import asyncio
    import os
    from datetime import timedelta
    from uuid import uuid4

    from google.protobuf.duration_pb2 import Duration
    from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.worker import Replayer

    from Sprout.config.loader import default_settings
    from Sprout.evolution.coding_profile import coding_profile
    from Sprout.evolution.isolated_replay import git, prepare
    from Sprout.evolution.regression_spec import freeze
    from Sprout.evolution.replay_config import configuration_contract
    from Sprout.evolution.replay_oracle import snapshot
    from Sprout.evolution.replay_plan import build_plan
    from Sprout.evolution.replay_snapshot import runtime_snapshot
    from Sprout.execution.coding_dispatch import IsolatedNodeDispatcher
    from Sprout.gateway.local_identity import creator_record, current_cli_principal
    from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeType
    from Sprout.orchestration.temporal_workflows import SproutExecutionWorkflow
    from Sprout.runtime.factory import create_runtime
    from Sprout.task.models import Task, TaskStatus

    host = os.getenv("SPROUT_TEMPORAL_TEST_HOST")
    if not host:
        pytest.skip("Explicit isolated Temporal endpoint required")
    assert host == "127.0.0.1:17233"
    original = tmp_path / "original"
    original.mkdir()
    git(original, "init", "-q")
    (original / "app.py").write_text("value = 1\n", encoding="utf-8")
    (original / "tests").mkdir()
    (original / "tests/oracle.txt").write_text("frozen fixture", encoding="utf-8")
    git(original, "add", ".")
    git(original, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", "fixture baseline")
    settings = default_settings()
    settings.model.provider = "echo"
    directory, repo, revision, settings = prepare(
        original, "HEAD", tmp_path / "experiments", settings)
    runtime = create_runtime(settings)
    runtime._isolated_coding_required = True
    image = "sha256:" + "a" * 64  # Binding placeholder; no image inspect/run in READ.
    runtime._isolated_coding_image = image
    command_attempts = []

    def deny_command(**kwargs):
        command_attempts.append(kwargs)
        raise ValueError("READ-only test has no commissioned command mapping")

    runtime._isolated_coding_dispatch = IsolatedNodeDispatcher(
        runtime.storage.metadata, runtime, deny_command)
    serving = None
    await runtime.start()
    try:
        workspace = await runtime.open_workspace(repo)
        principal = current_cli_principal()
        spec = {"cases": [{"id": "fixture", "target": "app:value", "expected_output": 1}]}
        plan = build_plan(source_task_id="explicit-read-boundary-fixture", revision=revision,
            mode="code", original=original, repo=repo, tests=("pytest",), regressions=("pytest",),
            oracle_paths=("tests",), frozen=snapshot(repo, ("tests",)), image=image,
            configuration=configuration_contract(settings), runtime=runtime_snapshot(runtime),
            creator=creator_record(principal), coding=coding_profile(image))
        task = Task(workspace_id=workspace.id, actor=principal, source="cli",
            status=TaskStatus.RUNNING, instruction="Read app.py for a fixed boundary test",
            metadata={"execution_mode": "code", "replay_plan": plan,
                      "replay_source_task_id": "explicit-read-boundary-fixture",
                      "acceptance_spec": spec, "required_paths": ["app.py"],
                      "regression_fingerprint": freeze(directory, spec)})
        metadata = runtime.storage.metadata
        await metadata.save_task(task)
        node = ExecutionNode(task_id=task.id, type=NodeType.READ)
        await metadata.save_execution_node(node)
        config = TemporalConfig(host=host, namespace="sprout-validation-" + uuid4().hex,
                                task_queue=experiment_task_queue(task))
        admin = await asyncio.wait_for(Client.connect(host), timeout=15)
        await admin.workflow_service.register_namespace(RegisterNamespaceRequest(
            namespace=config.namespace,
            workflow_execution_retention_period=Duration(seconds=86400)),
            timeout=timedelta(seconds=10))
        client = await asyncio.wait_for(
            Client.connect(host, namespace=config.namespace), timeout=15)
        worker = IsolatedCodingWorker(runtime, task, config)
        serving = asyncio.create_task(worker.run())
        graph = ExecutionGraph(task_id=task.id, nodes=(node,))
        backend = TemporalOrchestratorBackend(config, metadata=metadata)
        result = await asyncio.wait_for(backend.execute(graph), timeout=30)
        # Fixed READ graph only, not coding completion or growth validation.
        assert result.status == TaskStatus.COMPLETED
        persisted = await metadata.get_execution_node(node.id)
        assert persisted.attempts == 1
        assert persisted.metadata["execution_guard"]["state"] == "completed"
        output = persisted.metadata["output"]
        assert "value = 1" in str(output), output
        records = await metadata.list_task_submissions(task.id)
        assert len(records) == 1
        assert (await backend.execute(graph)).status == TaskStatus.COMPLETED
        assert await metadata.get_execution_node(node.id) == persisted
        assert await metadata.list_task_submissions(task.id) == records
        assert not await runtime.list_change_proposals(task.id)
        history = await client.get_workflow_handle(records[0]["id"]).fetch_history(
            rpc_timeout=timedelta(seconds=10))
        (directory / "history.json").write_text(history.to_json(), encoding="utf-8")
        replay = await asyncio.wait_for(
            Replayer(workflows=[SproutExecutionWorkflow]).replay_workflow(history), timeout=20)
        assert replay.replay_failure is None
        assert command_attempts == []
        assert (original / "app.py").read_text() == (repo / "app.py").read_text()
    finally:
        if serving is not None:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
        await runtime.stop()
