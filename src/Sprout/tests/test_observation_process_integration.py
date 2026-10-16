"""A querying process exits without owning/cancelling a separate worker."""

import asyncio
import json
import os
import sys
from datetime import timedelta
from uuid import uuid4

import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client

from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeType
from Sprout.orchestration.terminal.temporal import TemporalConfig, TemporalOrchestratorBackend


async def test_observer_exit_does_not_stop_worker(tmp_path):
    host = os.getenv("SPROUT_TEMPORAL_TEST_HOST")
    if not host:
        pytest.skip("Dedicated Temporal required; no paid model calls")
    assert host == "127.0.0.1:17233"
    namespace, queue = "sprout-validation-" + uuid4().hex, "observe-" + uuid4().hex
    admin = await Client.connect(host)
    await admin.workflow_service.register_namespace(RegisterNamespaceRequest(
        namespace=namespace, workflow_execution_retention_period=Duration(seconds=86400)),
        timeout=timedelta(seconds=10))
    client = await Client.connect(host, namespace=namespace)
    record = {"host": host, "namespace": namespace, "task_queue": queue,
              "id": "observe-" + uuid4().hex, "state": "accepted", "accepted": True}
    path = tmp_path / "accepted.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    prefix = [sys.executable, "-m", "Sprout.tests.observation_process_fixture"]
    worker = await asyncio.create_subprocess_exec(*prefix, "worker", str(path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    async def observe():
        child = await asyncio.create_subprocess_exec(*prefix, "observe", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=20)
            assert child.returncode == 0, stderr
            assert child.pid != worker.pid
            return json.loads(stdout)
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()

    try:
        assert (await asyncio.wait_for(worker.stdout.readline(), timeout=20)).strip() == b"ready"
        backend = TemporalOrchestratorBackend(
            TemporalConfig(host=host, namespace=namespace, task_queue=queue),
            wait_for_result=False)
        submitted = await backend.execute(ExecutionGraph(task_id="fixture", nodes=(
            ExecutionNode(id="fixture-node", task_id="fixture", type=NodeType.AGENT),)))
        assert submitted.metrics["result_waited"] is False
        record = backend._records[submitted.metrics["submission_id"]]
        handle = client.get_workflow_handle(record["id"], run_id=record["run_id"])
        path.write_text(json.dumps(record), encoding="utf-8")
        async with asyncio.timeout(20):
            while not (tmp_path / "entered").exists():
                await asyncio.sleep(0.05)
        first = await observe()
        assert first["observation"] == "running"
        assert worker.returncode is None
        (tmp_path / "release").touch()
        result = await asyncio.wait_for(handle.result(), timeout=20)
        assert result["status"] == "completed"
        second = await observe()
        assert second["result"]["status"] == "completed"
        assert worker.returncode is None
        history = await handle.fetch_history(rpc_timeout=timedelta(seconds=10))
        assert sum(event.HasField("activity_task_scheduled_event_attributes")
                   for event in history.events) == 1
        (tmp_path / "observation-evidence.json").write_text(json.dumps({
            "first": first, "second": second, "worker_survived_observer_exit": True,
            "activity_count": 1, "model_requests": 0}, indent=2), encoding="utf-8")
    finally:
        (tmp_path / "release").touch(exist_ok=True)
        if worker.returncode is None:
            worker.stdin.write(b"stop\n")
            await worker.stdin.drain()
        try:
            await asyncio.wait_for(worker.communicate(), timeout=15)
            assert worker.returncode == 0
        finally:
            if worker.returncode is None:
                worker.kill()
                await worker.wait()
