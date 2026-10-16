"""Formal submit/worker/status/sync processes; offline model, real Docker/Temporal."""

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

from Sprout.config.loader import default_settings
from Sprout.evolution.coding_profile import coding_profile
from Sprout.evolution.isolated_replay import git, prepare
from Sprout.evolution.regression_spec import freeze
from Sprout.evolution.replay_config import configuration_contract
from Sprout.evolution.replay_oracle import snapshot
from Sprout.evolution.replay_plan import build_plan
from Sprout.evolution.replay_snapshot import runtime_snapshot
from Sprout.gateway.local_identity import creator_record, current_cli_principal
from Sprout.orchestration.models import NodeType
from Sprout.runtime.factory import create_runtime
from Sprout.task.models import Task, TaskBudget, TaskStatus
from Sprout.tests.test_coding_docker_integration import inspect_and_remove_owned_stopped


async def test_formal_detached_coding_flow(tmp_path):
    host, image = os.getenv("SPROUT_TEMPORAL_TEST_HOST"), os.getenv("SPROUT_CODING_TEST_IMAGE")
    if not host or not image:
        pytest.skip("Explicit dedicated Temporal and pinned local Docker image required")
    assert host == "127.0.0.1:17233"
    namespace = "sprout-validation-" + uuid4().hex
    admin = await Client.connect(host)
    await admin.workflow_service.register_namespace(RegisterNamespaceRequest(
        namespace=namespace, workflow_execution_retention_period=Duration(seconds=86400)),
        timeout=timedelta(seconds=10))
    original = tmp_path / "original"
    original.mkdir()
    git(original, "init", "-q")
    (original / "app.py").write_text("def add(a,b): return 0\n", encoding="utf-8")
    (original / "test_smoke.py").write_text(
        "import importlib.util\nfrom pathlib import Path\ndef test_add():\n"
        "    spec=importlib.util.spec_from_file_location('app', Path('app.py'))\n"
        "    app=importlib.util.module_from_spec(spec); spec.loader.exec_module(app)\n"
        "    assert app.add(2,3)==5\n", encoding="utf-8")
    (original / "oracles").mkdir()
    (original / "oracles/frozen.txt").write_text("unchanged", encoding="utf-8")
    git(original, "add", ".")
    git(original, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", "fixture baseline")
    settings = default_settings()
    settings.model.provider = "echo"
    directory, repo, revision, settings = prepare(original, "HEAD", tmp_path / "experiments",
                                                 settings)
    config = directory / "source.toml"
    slots = ("metadata", "operational", "session", "context", "usage", "knowledge", "cache",
             "vectors", "graph", "trajectory_dir", "blobs_dir", "project_root")
    config.write_text('skills_dir = ' + json.dumps(settings.skills_dir) +
        '\n[model]\nprovider = "echo"\n[storage]\n' +
        "\n".join(key + " = " + json.dumps(getattr(settings.storage, key)) for key in slots) +
        '\n[storage.observations]\nenabled = false\n[memory]\nhome = ' +
        json.dumps(settings.memory.home) + '\n[security.audit]\npath = ' +
        json.dumps(settings.security.audit.path) + "\n", encoding="utf-8")
    runtime = create_runtime(settings)
    metadata = runtime.storage.metadata
    worker = None
    processes = []

    async def invoke(command, *options):
        child = await asyncio.create_subprocess_exec(sys.executable, "-m", "Sprout", "evolution",
            command, task.id, "--database", str(directory / "metadata.db"), *map(str, options),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(child.communicate(), timeout=45)
            assert child.returncode == 0, (out, err)
            processes.append({"command": command, "pid": child.pid})
            return json.loads(out)
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()

    try:
        workspace = await runtime.open_workspace(repo)
        principal = current_cli_principal()
        acceptance = {"cases": [{"id": "normal", "target": "app:add", "args": [2, 3],
                                  "expected_output": 5}]}
        regression = {"cases": [{"id": "zero", "target": "app:add", "args": [-1, 1],
                                  "expected_output": 0}]}
        plan = build_plan(source_task_id="detached-offline-trial", revision=revision, mode="code",
            original=original, repo=repo, tests=("pytest",), regressions=("pytest",),
            oracle_paths=("oracles",), frozen=snapshot(repo, ("oracles",)), image=image,
            configuration=configuration_contract(settings), runtime=runtime_snapshot(runtime),
            creator=creator_record(principal), coding=coding_profile(image))
        task = Task(workspace_id=workspace.id, actor=principal, source="cli",
            instruction="Implement integer addition in app.py", status=TaskStatus.CREATED,
            budget=TaskBudget(max_model_calls=3),
            metadata={"execution_mode": "code", "replay_plan": plan,
                "replay_source_task_id": "detached-offline-trial", "acceptance_spec": acceptance,
                "required_paths": ["app.py"], "require_acceptance_tests": True,
                "regression_fingerprint": freeze(directory, regression)})
        await metadata.save_task(task)
        submitted = await invoke("coding-submit", "--config", config,
            "--temporal-host", host, "--temporal-namespace", namespace)
        assert submitted["metrics"]["submission_state"] == "accepted"
        assert submitted["worker_started"] is False
        before = await invoke("coding-status")
        assert before["observation"] == "running"
        worker = await asyncio.create_subprocess_exec(sys.executable, "-m",
            "Sprout.tests.detached_coding_worker_fixture", "evolution", "coding-worker", task.id,
            "--database", str(directory / "metadata.db"), "--config", str(config),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        observed = await invoke("coding-status", "--wait-seconds", "30")
        assert observed["result"]["status"] == "waiting_approval", observed
        assert worker.returncode is None
        nodes = await metadata.list_execution_nodes(task.id)
        assert next(node for node in nodes if node.type is NodeType.EVALUATION).metadata[
            "output"]["verification_report"]["verified"] is True
        synced = await invoke("coding-sync")
        assert synced["task_status"] == "waiting_approval"
        assert synced["database_role"] == "metadata" and synced["nodes_modified"] is False
        assert (await metadata.get_task(task.id)).status is TaskStatus.WAITING_APPROVAL
        repeated = await invoke("coding-submit", "--config", config,
            "--temporal-host", host, "--temporal-namespace", namespace)
        assert repeated["new_submission"] is False
        assert await metadata.list_execution_nodes(task.id) == nodes
        assert len(await metadata.list_task_submissions(task.id)) == 1
        budget = await metadata.model_budget_summary(task.id)
        assert budget["consumed"] == budget["responded"] == budget["call_limit"] == 3
        report = await invoke("coding-report")
        assert report["stage"] == "code_review"
        assert report["budget"]["remaining"] == 0
        assert report["budget"]["consumed"] == 3
        assert report["verification"]["reported_verified"] is True
        assert report["commit_requests"] == []
        assert worker.returncode is None
        assert git(original, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD") == revision
        assert (original / "app.py").read_text() == "def add(a,b): return 0\n"
        (directory / "detached-acceptance.json").write_text(json.dumps({
            "task_id": task.id, "processes": processes, "worker_pid": worker.pid,
            "verified": True, "task_status": synced["task_status"], "model_requests": 0,
            "persistent_model_budget": budget,
            "policy_scope": "explicit offline fixture", "git_applied": False}, indent=2),
            encoding="utf-8")
    finally:
        if worker is not None:
            if worker.returncode is None:
                worker.stdin.write(b"interrupt\n")
                await worker.stdin.drain()
            try:
                out, err = await asyncio.wait_for(worker.communicate(), timeout=20)
                assert worker.returncode == 0, (out, err)
            finally:
                if worker.returncode is None:
                    worker.kill()
                    await worker.wait()
        try:
            rows = await metadata._db.fetchall("SELECT record_json,state FROM coding_executions")
            for row in rows:
                record = json.loads(row["record_json"])
                command = record.get("command")
                if row["state"] == "completed" and command:
                    inspect_and_remove_owned_stopped(
                        command[command.index("--name") + 1], directory)
        finally:
            await runtime.storage.close()
