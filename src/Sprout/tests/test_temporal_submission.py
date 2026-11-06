"""Fault injection: transport uncertainty is not proof of task failure."""

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

from Sprout.orchestration.models import ExecutionGraph
from Sprout.orchestration.terminal.temporal import TemporalOrchestratorBackend
from Sprout.runtime.queue import TemporalTaskQueue
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus


async def test_worker_confirmation_survives_late_start_failure(tmp_path):
    store = open_metadata_store(str(tmp_path / "confirmation.db"))
    task = Task(workspace_id="fixture")
    try:
        await store.save_task(task)
        record = {"id": "workflow", "task_id": task.id, "state": "pending",
                  "namespace": "fixture", "task_queue": "fixture", "payload": {"nodes": []}}
        await store.ensure_task_submission(record)
        assert not await store.confirm_temporal_activity(
            {**record, "task_queue": "wrong"}, "wrong-run")
        assert (await store.get_task(task.id)).status == task.status
        assert await store.confirm_temporal_activity(record, "original-run")
        await store.update_task_submission({**record, "state": "uncertain"})
        await store.park_unconfirmed_temporal_start(task.id, record["id"])
        assert not await store.confirm_temporal_activity(record, "wrong-run")
        saved, = await store.list_task_submissions(task.id)
        assert saved["accepted"] and saved["run_id"] == "original-run"
        assert (await store.get_task(task.id)).status == TaskStatus.RUNNING
    finally:
        store.close()


async def test_worker_confirmation_concurrent_runs_and_rollback(tmp_path):
    import asyncio
    import sqlite3

    store = open_metadata_store(str(tmp_path / "concurrent.db"))
    task = Task(workspace_id="fixture")
    try:
        await store.save_task(task)
        record = {"id": "workflow", "task_id": task.id, "state": "pending",
                  "namespace": "fixture", "task_queue": "fixture", "payload": {"nodes": []}}
        await store.ensure_task_submission(record)
        await store._db.execute(
            "CREATE TRIGGER reject_running BEFORE UPDATE OF status ON tasks "
            "WHEN NEW.status = 'running' BEGIN SELECT RAISE(ABORT, 'test rollback'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="test rollback"):
            await store.confirm_temporal_activity(record, "rollback-run")
        assert await store.list_task_submissions(task.id) == [record]
        assert (await store.get_task(task.id)).status == task.status
        await store._db.execute("DROP TRIGGER reject_running")
        confirmed = await asyncio.gather(
            store.confirm_temporal_activity(record, "first"),
            store.confirm_temporal_activity(record, "second"),
        )
        assert sorted(confirmed) == [False, True]
        saved, = await store.list_task_submissions(task.id)
        assert await store.confirm_temporal_activity(record, saved["run_id"])
        await store.update_task_submission({**saved, "state": "resolved"})
        assert not await store.confirm_temporal_activity(record, saved["run_id"])
        terminal_record = {**record, "id": "terminal"}
        await store.ensure_task_submission(terminal_record)
        await store.update_task_status(task.id, TaskStatus.COMPLETED)
        assert not await store.confirm_temporal_activity(terminal_record, "terminal-run")
        assert (await store.get_task(task.id)).status == TaskStatus.COMPLETED
    finally:
        store.close()


@pytest.mark.parametrize("changed", ["workflow_id", "workflow_run_id", "task_queue",
                                     "namespace", "activity_type", "payload",
                                     "missing_workflow", "missing_run", "zero_rows", "task_id"])
async def test_activity_rejects_mismatched_submission(changed):
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from temporalio.testing import ActivityEnvironment

    from Sprout.orchestration.activities.node import bind_node_activity

    node = {"task_id": "task", "id": "node"}
    record = {"id": "workflow", "task_id": "task", "namespace": "namespace", "task_queue": "queue",
              "run_id": "run", "payload": {"nodes": [node]}}
    metadata = SimpleNamespace(list_task_submissions=AsyncMock(return_value=[record]),
                               get_task_submission=AsyncMock(return_value=record),
                               confirm_temporal_activity=AsyncMock(return_value=False))
    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=metadata),
                              execute_node=AsyncMock())
    environment = ActivityEnvironment()
    identity = {"workflow_id": "workflow", "workflow_run_id": "run",
                "workflow_type": "SproutExecutionWorkflow", "namespace": "namespace",
                "task_queue": "queue", "activity_type": "run_sprout_node", "is_local": False}
    if changed == "payload":
        node = {**node, "id": "foreign-node"}
    elif changed == "task_id":
        node = {**node, "task_id": "foreign-task"}
        metadata.list_task_submissions.return_value = []
    elif changed == "missing_workflow":
        identity["workflow_id"] = None
    elif changed == "missing_run":
        identity["workflow_run_id"] = None
    elif changed != "zero_rows":
        identity[changed] = "foreign"
    environment.info = replace(environment.info, **identity)
    with pytest.raises(PermissionError, match="persisted submission"):
        await environment.run(bind_node_activity(runtime), node)
    assert metadata.confirm_temporal_activity.await_count == int(changed == "zero_rows")
    runtime.execute_node.assert_not_awaited()


def _client(monkeypatch, client=None, error=None):
    async def connect(*args, **kwargs):
        if error:
            raise error
        return client

    monkeypatch.setattr("temporalio.client.Client.connect", connect)


async def test_connect_failure_does_not_leave_a_local_executable_task(monkeypatch):
    _client(monkeypatch, error=ConnectionError("offline"))
    queue = TemporalTaskQueue()
    try:
        await queue.put(Task(workspace_id="fixture"))
    except (ConnectionError, RuntimeError):
        pass
    assert queue.empty(), "Failed remote submission leaked into local execution FIFO"


@pytest.mark.parametrize("phase", ["connect", "start", "result"])
async def test_transport_failure_is_not_a_terminal_task_failure(tmp_path, monkeypatch, phase):
    class Handle:
        async def result(self):
            if phase == "result":
                raise TimeoutError("result acknowledgement lost")
            return {"status": "completed"}

    class Client:
        async def start_workflow(self, *args, **kwargs):
            if phase == "start":
                raise TimeoutError("start acknowledgement lost")
            return Handle()

    _client(monkeypatch, Client(), ConnectionError("offline") if phase == "connect" else None)
    store = open_metadata_store(str(tmp_path / "control.db"))
    task = Task(
        workspace_id="fixture", instruction="read", metadata={"execution_mode": "read_only"}
    )
    await store.save_task(task)
    try:
        result = await TemporalOrchestratorBackend(metadata=store).execute(
            ExecutionGraph(task_id=task.id)
        )
        assert result.status != TaskStatus.FAILED, (
            "A transport failure does not prove execution failed"
        )
        assert (await store.get_task(task.id)).status != TaskStatus.FAILED
    finally:
        store.close()


async def test_already_started_submission_attaches_instead_of_failing(monkeypatch):
    class Handle:
        async def result(self):
            return {"status": "completed"}

    class Client:
        async def start_workflow(self, *args, **kwargs):
            raise WorkflowAlreadyStartedError(kwargs["id"], "SproutExecutionWorkflow", run_id="run")

        def get_workflow_handle(self, workflow_id, **kwargs):
            return Handle()

    _client(monkeypatch, Client())
    result = await TemporalOrchestratorBackend().execute(ExecutionGraph(task_id="duplicate"))
    assert result.status == TaskStatus.COMPLETED


async def test_closed_workflow_identity_cannot_be_reused_for_duplicate_execution(monkeypatch):
    from temporalio.common import WorkflowIDReusePolicy

    class Handle:
        async def result(self):
            return {"status": "completed"}

    class Client:
        executions = 0

        async def start_workflow(self, *args, **kwargs):
            if (
                self.executions
                and kwargs.get("id_reuse_policy") == WorkflowIDReusePolicy.REJECT_DUPLICATE
            ):
                raise WorkflowAlreadyStartedError(
                    kwargs["id"], "SproutExecutionWorkflow", run_id="run"
                )
            self.executions += 1
            return Handle()

        def get_workflow_handle(self, workflow_id, **kwargs):
            return Handle()

    client = Client()
    _client(monkeypatch, client)
    backend = TemporalOrchestratorBackend()
    graph = ExecutionGraph(task_id="same-task")
    assert (await backend.execute(graph)).status == TaskStatus.COMPLETED
    assert (await backend.execute(graph)).status == TaskStatus.COMPLETED
    assert client.executions == 1, "Duplicate submission started another execution"


async def test_restart_reuses_frozen_payload_and_destination(tmp_path, monkeypatch):
    from Sprout.orchestration.terminal.temporal import TemporalConfig

    path = str(tmp_path / "control.db")
    store = open_metadata_store(path)
    task = Task(workspace_id="fixture")
    await store.save_task(task)
    _client(monkeypatch, error=ConnectionError("offline"))
    graph = ExecutionGraph(task_id=task.id)
    result = await TemporalOrchestratorBackend(
        TemporalConfig(host="original", namespace="original"), metadata=store
    ).execute(graph)
    assert result.status == TaskStatus.WAITING_RESOURCE
    original = (await store.list_task_submissions(task.id))[0]
    assert original["state"] == "pending"
    assert (await store.get_task(task.id)).status == TaskStatus.WAITING_RESOURCE
    store.close()

    class Handle:
        async def result(self):
            return {"status": "completed"}

    class Client:
        async def start_workflow(self, name, payload, **kwargs):
            assert payload == original["payload"]
            assert kwargs["id"] == original["id"]
            return Handle()

    async def connect(host, **kwargs):
        assert host == "original"
        assert kwargs["namespace"] == "original"
        return Client()

    monkeypatch.setattr("temporalio.client.Client.connect", connect)
    store = open_metadata_store(path)
    try:
        backend = TemporalOrchestratorBackend(TemporalConfig(host="changed"), metadata=store)
        result = await backend.execute(ExecutionGraph(task_id=task.id, dispatch_key="changed"))
        assert result.status == TaskStatus.COMPLETED
        assert len(await store.list_task_submissions(task.id)) == 1
        assert (await store.get_task(task.id)).status == TaskStatus.COMPLETED
        confirmed = (await store.list_task_submissions(task.id))[0]
        await store.update_task_submission({**confirmed, "state": "uncertain"})
        assert (await store.list_task_submissions(task.id))[0]["state"] == "resolved"
    finally:
        store.close()


async def test_start_ack_loss_reconnects_to_existing_run(tmp_path, monkeypatch):
    class Handle:
        async def result(self):
            return {"status": "completed"}

    class Client:
        started = None
        calls = 0

        async def start_workflow(self, name, payload, **kwargs):
            self.calls += 1
            if self.started is None:
                self.started = kwargs["id"]
                raise TimeoutError("accepted but acknowledgement lost")
            assert kwargs["id"] == self.started
            raise WorkflowAlreadyStartedError(self.started, name, run_id="accepted-run")

        def get_workflow_handle(self, workflow_id, **kwargs):
            assert workflow_id == self.started
            assert kwargs["run_id"] == "accepted-run"
            return Handle()

    client = Client()
    _client(monkeypatch, client)
    path = str(tmp_path / "control.db")
    store = open_metadata_store(path)
    task = Task(workspace_id="fixture")
    await store.save_task(task)
    graph = ExecutionGraph(task_id=task.id)
    assert (
        await TemporalOrchestratorBackend(metadata=store).execute(graph)
    ).status == TaskStatus.WAITING_RESOURCE
    assert (await store.list_task_submissions(task.id))[0]["state"] == "uncertain"
    store.close()
    store = open_metadata_store(path)
    try:
        backend = TemporalOrchestratorBackend(metadata=store)
        assert (await backend.execute(graph)).status == TaskStatus.COMPLETED
        assert (await backend.execute(graph)).status == TaskStatus.COMPLETED
        assert client.calls == 2
    finally:
        store.close()


async def test_approval_round_requires_changed_decision_key(monkeypatch):
    class Handle:
        async def result(self):
            return {"status": "waiting_approval"}

    class Client:
        ids = []

        async def start_workflow(self, *args, **kwargs):
            self.ids.append(kwargs["id"])
            return Handle()

    client = Client()
    _client(monkeypatch, client)
    backend = TemporalOrchestratorBackend()
    graph = ExecutionGraph(task_id="approval")
    assert (await backend.execute(graph)).status == TaskStatus.WAITING_APPROVAL
    assert (await backend.execute(graph)).status == TaskStatus.WAITING_APPROVAL
    await backend.execute(ExecutionGraph(task_id="approval", dispatch_key="human-decision"))
    assert len(client.ids) == 2
    assert client.ids[0] != client.ids[1]


async def test_bound_temporal_queue_never_enqueues_local_execution():
    submitted = []

    async def submit(task):
        submitted.append(task.id)

    queue = TemporalTaskQueue()
    queue.bind(submit)
    task = Task(workspace_id="fixture")
    await queue.put(task)
    assert submitted == [task.id]
    assert queue.empty()


async def test_transport_error_never_calls_local_handler(monkeypatch):
    _client(monkeypatch, error=ConnectionError("offline"))

    async def handler(*args, **kwargs):
        pytest.fail("Temporal submission must not fall back to local execution")

    result = await TemporalOrchestratorBackend().execute(
        ExecutionGraph(task_id="no-fallback"), handler=handler
    )
    assert result.status == TaskStatus.WAITING_RESOURCE


async def test_resume_skips_completed_evaluation_edit_rounds(monkeypatch):
    from Sprout.orchestration.temporal_workflows import SproutExecutionWorkflow

    called = []

    async def execute_activity(activity, node, **kwargs):
        called.append(node["id"])
        return {"status": "completed", "output": {}}

    monkeypatch.setattr("temporalio.workflow.execute_activity", execute_activity)
    monkeypatch.setattr("temporalio.workflow.patched", lambda _: True)
    result = await SproutExecutionWorkflow().run(
        {
            "nodes": [
                {
                    "id": "eval",
                    "type": "evaluation",
                    "status": "completed",
                    "metadata": {"output": {"status": "passed"}},
                },
                {"id": "edit", "type": "agent", "status": "pending"},
                {"id": "test", "type": "evaluation", "status": "pending"},
                {"id": "apply", "type": "apply", "status": "pending"},
            ]
        }
    )
    assert result["status"] == "completed"
    assert called == ["apply"]


async def test_cancelled_task_never_resubmits(tmp_path, monkeypatch):
    _client(monkeypatch, error=AssertionError("Must not contact Temporal"))
    store = open_metadata_store(str(tmp_path / "control.db"))
    task = Task(workspace_id="fixture", status=TaskStatus.CANCELLED)
    await store.save_task(task)
    try:
        result = await TemporalOrchestratorBackend(metadata=store).execute(
            ExecutionGraph(task_id=task.id)
        )
        assert result.status == TaskStatus.CANCELLED
        assert await store.list_task_submissions(task.id) == []
    finally:
        store.close()


async def test_activity_ack_retry_returns_persisted_output_without_execution(tmp_path):
    from types import SimpleNamespace

    from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
    from Sprout.runtime.runtime import Runtime
    from Sprout.workspace.models import Workspace

    store = open_metadata_store(str(tmp_path / "control.db"))
    workspace = Workspace(id="fixture", root=tmp_path)
    task = Task(workspace_id=workspace.id, metadata={"execution_mode": "read_only"})
    node = ExecutionNode(
        task_id=task.id,
        type=NodeType.AGENT,
        status=NodeStatus.COMPLETED,
        metadata={"output": {"text": "saved"}},
    )
    await store.save_workspace(workspace)
    await store.save_task(task)
    await store.save_execution_node(node)
    runtime = Runtime.__new__(Runtime)
    runtime.storage = SimpleNamespace(metadata=store)
    try:
        result = await runtime.execute_node(
            ExecutionNode(id=node.id, task_id=task.id, type=node.type)
        )
        assert result["output"] == {"text": "saved"}
        assert result["status"] == "completed"
    finally:
        store.close()


async def test_result_retry_only_attaches_and_never_starts_again(tmp_path, monkeypatch):
    class Handle:
        first_execution_run_id = "known-run"

        async def result(self):
            raise TimeoutError("result not available")

    class Client:
        starts = 0
        attaches = 0

        async def start_workflow(self, *args, **kwargs):
            self.starts += 1
            return Handle()

        def get_workflow_handle(self, workflow_id, **kwargs):
            assert kwargs["run_id"] == "known-run"
            self.attaches += 1
            return Handle()

    client = Client()
    _client(monkeypatch, client)
    path = str(tmp_path / "control.db")
    store = open_metadata_store(path)
    graph = ExecutionGraph(task_id="known")
    await TemporalOrchestratorBackend(metadata=store).execute(graph)
    store.close()
    store = open_metadata_store(path)
    try:
        result = await TemporalOrchestratorBackend(metadata=store).execute(graph)
        assert result.status == TaskStatus.WAITING_RESOURCE
        assert client.starts == 1
        assert client.attaches == 1
    finally:
        store.close()


def test_cli_submission_query_uses_gateway_without_scheduler(monkeypatch):
    from typer.testing import CliRunner

    from Sprout.cli.commands import project

    class Gateway:
        async def task_submissions(self, task_id):
            assert task_id == "known"
            return [{"id": "workflow", "state": "uncertain"}]

    monkeypatch.setattr(project, "_gateway", lambda config: Gateway())
    result = CliRunner().invoke(project.app, ["task-submissions", "known"])
    assert result.exit_code == 0, result.output
    assert '"state": "uncertain"' in result.output


async def test_worker_connect_failure_closes_runtime(monkeypatch):
    from Sprout.orchestration.worker import run_worker

    class Runtime:
        stopped = False

        async def start(self):
            pass

        async def stop(self):
            self.stopped = True

    async def ready(settings):
        pass

    runtime = Runtime()
    monkeypatch.setattr("Sprout.config.loader.load_settings", lambda: None)
    monkeypatch.setattr("Sprout.storage.bootstrap.ensure_storage_ready", ready)
    monkeypatch.setattr("Sprout.runtime.factory.create_runtime", lambda settings: runtime)
    _client(monkeypatch, error=ConnectionError("offline"))
    with pytest.raises(ConnectionError):
        await run_worker()
    assert runtime.stopped


async def test_generic_workers_keep_node_and_growth_runtime_isolated(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from temporalio import activity

    from Sprout.orchestration.activities import node
    from Sprout.orchestration.terminal.temporal import TemporalConfig
    from Sprout.orchestration.worker import run_worker

    ready = {key: asyncio.Event() for key in ("a", "b")}
    exit_worker = {key: asyncio.Event() for key in ready}
    registered = {}

    class Runtime:
        def __init__(self, key):
            self.key = key
            self.storage = SimpleNamespace(metadata=object())
            self.running = False

        async def start(self):
            self.running = True

        async def stop(self):
            self.running = False

        async def execute_node(self, request):
            await asyncio.sleep(0)
            assert self.running
            return {"node_id": request.id, "runtime": self.key}

    class Growth:
        def __init__(self, metadata, workspace_id, trajectory_dir, *, runtime):
            assert metadata is runtime.storage.metadata
            self.runtime = runtime
            self.output = {"workspace": workspace_id, "directory": trajectory_dir,
                           "runtime": runtime.key}

        async def run_once(self):
            await asyncio.sleep(0)
            assert self.runtime.running
            return self.output

    class Worker:
        def __init__(self, client, *, task_queue, workflows, activities):
            self.key = task_queue
            registered[self.key] = {
                activity._Definition.must_from_callable(fn).name: fn for fn in activities
            }

        async def run(self):
            ready[self.key].set()
            await exit_worker[self.key].wait()

    async def prepare(settings):
        pass

    runtimes = {key: Runtime(key) for key in ready}
    pending = iter(runtimes.values())
    sentinel = object()
    monkeypatch.setattr(node, "_activity_runtime", sentinel)
    monkeypatch.setattr("Sprout.config.loader.load_settings", lambda: None)
    monkeypatch.setattr("Sprout.storage.bootstrap.ensure_storage_ready", prepare)
    monkeypatch.setattr("Sprout.runtime.factory.create_runtime", lambda settings: next(pending))
    monkeypatch.setattr("Sprout.evolution.automation.GrowthAutomation", Growth)
    monkeypatch.setattr("temporalio.worker.Worker", Worker)
    _client(monkeypatch, object())

    async def check(key):
        activities = registered[key]
        assert set(activities) == {"run_sprout_node", "run_growth_automation"}
        request = {"id": key, "type": "read", "created_at": "2026-11-05T00:00:00+00:00"}
        assert await activities["run_sprout_node"](request) == {
            "node_id": key, "runtime": key,
        }
        assert await activities["run_growth_automation"](key, "traces") == {
            "workspace": key, "directory": "traces", "runtime": key,
        }

    workers = {
        key: asyncio.create_task(run_worker(TemporalConfig(task_queue=key))) for key in ready
    }
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready.values())), 5)
        await asyncio.gather(check("a"), check("b"))
        exit_worker["a"].set()
        await workers["a"]
        assert not runtimes["a"].running
        await check("b")
        assert node._activity_runtime is sentinel
    finally:
        for event in exit_worker.values():
            event.set()
        await asyncio.gather(*workers.values())
    assert not runtimes["b"].running


@pytest.mark.parametrize("partially_started", [False, True])
async def test_generic_worker_start_failure_does_not_register_activities(
    monkeypatch, partially_started,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from Sprout.orchestration.activities import node
    from Sprout.orchestration.worker import run_worker

    sentinel = object()
    monkeypatch.setattr(node, "_activity_runtime", sentinel)
    close = AsyncMock()
    runtime = SimpleNamespace(
        start=AsyncMock(side_effect=RuntimeError("startup failed")),
        stop=AsyncMock(side_effect=close),
        storage=SimpleNamespace(close=close),
        _started=partially_started,
    )
    connect = AsyncMock()
    worker = Mock()
    monkeypatch.setattr("Sprout.config.loader.load_settings", lambda: None)
    monkeypatch.setattr("Sprout.storage.bootstrap.ensure_storage_ready", AsyncMock())
    monkeypatch.setattr("Sprout.runtime.factory.create_runtime", lambda settings: runtime)
    monkeypatch.setattr("temporalio.client.Client.connect", connect)
    monkeypatch.setattr("temporalio.worker.Worker", worker)
    with pytest.raises(RuntimeError, match="startup failed"):
        await run_worker()
    connect.assert_not_called()
    worker.assert_not_called()
    assert node._activity_runtime is sentinel
    close.assert_awaited_once()
    assert runtime.stop.await_count == int(partially_started)


@pytest.mark.parametrize("changed", [None, "id", "workspace_id", "source", "scope", "owner"])
async def test_ack_recovery_admits_only_the_same_node_product(changed):
    from dataclasses import dataclass, replace
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
    from Sprout.runtime.runtime import Runtime
    from Sprout.task.models import DelegationScope, Task

    @dataclass
    class Workspace:
        manifest: object = None

    task = Task(workspace_id="workspace", instruction="Read", status=TaskStatus.RUNNING)
    producer = ExecutionNode(task_id=task.id, type=NodeType.READ, status=NodeStatus.COMPLETED,
                             metadata={"output": [], "execution_guard": {"owner": "owner"}})
    snapshot = replace(task, status=TaskStatus.WAITING_RESOURCE)
    if changed in {"id", "workspace_id", "source"}:
        snapshot = replace(snapshot, **{changed: "other"})
    if changed == "scope":
        snapshot = replace(snapshot, delegation_scope=DelegationScope(allowed_paths=("other",)))
    recorded_node = producer
    if changed == "owner":
        recorded_node = replace(producer, metadata={"execution_guard": {"owner": "other"}})
    runtime = SimpleNamespace(_persist_node_products=AsyncMock(), _node_products={})
    scope = SimpleNamespace(task=task, workspace=Workspace(),
                            node=SimpleNamespace(dependencies=(producer.id,)))
    runtime._node_products[(task.id, producer.id)] = (
        snapshot, scope.workspace, recorded_node, [], (), runtime,
    )
    if changed is None:
        await Runtime._admit_node_products(runtime, scope, [producer])
        assert scope.captured_paths == ()
        assert runtime._node_products[(task.id, producer.id)][0] == task
    else:
        with pytest.raises(PermissionError, match="node_artifact_provenance_unavailable"):
            await Runtime._admit_node_products(runtime, scope, [producer])


async def test_unbound_worker_activities_fail_explicitly():
    from Sprout.orchestration.activities.growth import bind_growth_activity
    from Sprout.orchestration.activities.node import bind_node_activity

    with pytest.raises(RuntimeError, match="no runtime configured"):
        await bind_node_activity(None)({})
    with pytest.raises(RuntimeError, match="no runtime configured"):
        await bind_growth_activity(None)("workspace", "traces")


@pytest.mark.parametrize("change", ["none", "node", "kind", "reason", "malformed"])
async def test_waiting_diagnostic_is_narrow_and_never_retries(monkeypatch, change):
    output = {"waiting": True, "waiting_kind": "resource",
              "waiting_reason": "isolated_worker_not_assembled"}
    nodes = [{"node_id": "waiting-node", "output": output}]
    if change == "node":
        nodes[0]["node_id"] = "different-node"
    elif change == "kind":
        output["waiting_kind"] = "approval"
    elif change == "reason":
        output["waiting_reason"] = "untrusted-ready-to-retry"
    elif change == "malformed":
        nodes = None

    class Handle:
        async def result(self):
            return {"status": "waiting_resource", "node_id": "waiting-node", "nodes": nodes}

    class Client:
        starts = 0

        async def start_workflow(self, *args, **kwargs):
            self.starts += 1
            return Handle()

    client = Client()
    _client(monkeypatch, client)
    backend = TemporalOrchestratorBackend()
    graph = ExecutionGraph(task_id="waiting-fixture")
    assert (await backend.execute(graph)).status == TaskStatus.WAITING_RESOURCE
    record = next(iter(backend._records.values()))
    if change == "none":
        assert record["result"]["waiting_reason"] == "isolated_worker_not_assembled"
    else:
        assert "waiting_reason" not in record["result"]
    assert (await backend.execute(graph)).status == TaskStatus.WAITING_RESOURCE
    assert client.starts == 1
    assert len(backend._records) == 1
