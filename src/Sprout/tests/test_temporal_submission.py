"""Fault injection: transport uncertainty is not proof of task failure."""

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

from Sprout.orchestration.models import ExecutionGraph
from Sprout.orchestration.terminal.temporal import TemporalOrchestratorBackend
from Sprout.runtime.queue import TemporalTaskQueue
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus


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
