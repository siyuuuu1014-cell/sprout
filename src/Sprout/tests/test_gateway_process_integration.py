"""Real scheduler, independent Gateway clients and production node activities."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client

from Sprout.orchestration.submission_observation import observe_submission
from Sprout.tests.gateway_process_fixture import OWNER, make_runtime


@pytest.mark.parametrize("worker_mode", [
    "fixture", "cli", "full-cli", "full-cli-retry", "full-cli-code-policy", "full-cli-code-live",
])
async def test_gateway_submit_worker_observe_sync_processes(tmp_path, worker_mode):
    live = worker_mode == "full-cli-code-live"
    code_policy = worker_mode in {"full-cli-code-policy", "full-cli-code-live"}
    if live and os.getenv("SPROUT_NORMAL_POLICY_LIVE") != "1":
        pytest.skip("Explicit five-request real-model authorization required")
    host = os.getenv("SPROUT_TEMPORAL_TEST_HOST")
    if not host:
        pytest.skip("Dedicated Temporal required; no paid model calls")
    assert host == "127.0.0.1:17233"
    namespace, queue = "sprout-validation-" + uuid4().hex, "gateway-" + uuid4().hex
    admin = await asyncio.wait_for(Client.connect(host), timeout=15)
    await admin.workflow_service.register_namespace(RegisterNamespaceRequest(
        namespace=namespace, workflow_execution_retention_period=Duration(seconds=86400)),
        timeout=timedelta(seconds=10))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    if code_policy:
        from Sprout.evolution.isolated_replay import git

        git(root, "init", "-q")
        git(root, "add", "app.py")
        subprocess.run(["git", "-C", str(root), "-c", "core.hooksPath=nul",
                        "-c", "commit.gpgsign=false", "commit", "-qm", "Policy trial baseline"],
                       check=True, capture_output=True, timeout=20)
    runtime = make_runtime(tmp_path)
    try:
        settings = runtime._settings
        config_path = tmp_path / "worker.toml"
        storage = {key: getattr(settings.storage, key) for key in (
            "operational", "metadata", "session", "context", "usage", "knowledge",
            "trajectory_dir", "blobs_dir", "project_root")}
        config_path.write_text("skills_dir = " + json.dumps(settings.skills_dir) +
            '\n[model]\nprovider = "echo"\nmodel = "echo-1"\n[storage]\n' +
            "\n".join(key + " = " + json.dumps(value) for key, value in storage.items()) +
            '\n[storage.observations]\nenabled = false\n[memory]\nenabled = false\nhome = ' +
            json.dumps(settings.memory.home) + '\n[security.audit]\npath = ' +
            json.dumps(settings.security.audit.path) + "\n", encoding="utf-8")
        if live:
            from Sprout.config.loader import load_env_file, load_settings

            load_env_file()
            model = load_settings(os.environ["SPROUT_LIVE_CONFIG"]).model
            assert (model.provider == "openai_compatible" and model.model == "deepseek-flash"
                    and model.api_key_env == "DEEPSEEK_API_KEY"
                    and model.base_url.rstrip("/") == "https://api.deepseek.com"
                    and model.thinking_mode == "disabled")
            assert os.environ.get("DEEPSEEK_API_KEY", "").strip()
            model_block = '\n[model]\n' + "\n".join(
                key + " = " + json.dumps(getattr(model, key)) for key in
                ("provider", "model", "base_url", "api_key_env", "thinking_mode"))
            model_block += '\nmax_output_tokens = 8192\n[storage]\n'
            text = config_path.read_text(encoding="utf-8")
            text = text.replace('\n[model]\nprovider = "echo"\nmodel = "echo-1"\n[storage]\n',
                                model_block)
            config_path.write_text(text, encoding="utf-8")
        workspace = await runtime.open_workspace(root)
        if worker_mode.startswith("full-cli"):
            creator = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "Sprout.cli.app", "project", "task", "create",
                workspace.id, ("Add increment(value) returning value+1; test positive/negative/zero"
                               if code_policy else
                               "Explain the value; do not edit"), "--mode",
                "code" if code_policy else "read_only",
                "--config", str(config_path), stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(creator.communicate(), timeout=20)
                assert creator.returncode == 0, stderr.decode(errors="replace")
                task = await runtime.get_task(json.loads(stdout)["task_id"])
                if live:
                    from dataclasses import replace

                    task = replace(task, budget=replace(task.budget, max_model_calls=5))
                    await runtime.storage.metadata.save_task(task)
            finally:
                if creator.returncode is None:
                    creator.kill()
                    await creator.wait()
        else:
            task = await runtime.create_task_planned(workspace.id, "Explain the value; do not edit",
                actor=OWNER, source="sdk", execution_mode="read_only")
    finally:
        await runtime.stop()
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"host": host, "namespace": namespace,
        "task_queue": queue, "task_id": task.id}), encoding="utf-8")
    prefix = [sys.executable, "-m", "Sprout.tests.gateway_process_fixture"]
    pids = []
    first_submit = True

    async def child(role):
        nonlocal first_submit
        args = [*prefix, role, str(route)]
        if worker_mode.startswith("full-cli"):
            args = [sys.executable, "-m", "Sprout.cli.app", "project", "task", role,
                    task.id, "--config", str(config_path)]
            if role == "submit":
                args += ["--host", host, "--namespace", namespace, "--task-queue", queue]
                if worker_mode == "full-cli-retry" and first_submit:
                    args[2] = "Sprout.tests.pending_connection_cli_fixture"
                first_submit = False
            if role == "retry-submission":
                args += ["--submission-id", record["id"]]
            if role == "observe":
                args += ["--wait-seconds", "30"]
        process = await asyncio.create_subprocess_exec(*args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        pids.append(process.pid)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=50)
            assert process.returncode == 0, stderr.decode(errors="replace")
            return json.loads(stdout)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    submitted = await child("submit")
    assert submitted["new_submission"] and not submitted["worker_started"]
    runtime = make_runtime(tmp_path)
    try:
        record = (await runtime.get_task_submissions(task.id))[0]
    finally:
        await runtime.stop()
    if worker_mode == "full-cli-retry":
        assert record["state"] == "pending" and record["phase"] == "connect"
        recovered = await child("retry-submission")
        assert recovered["retry_requested"] and not recovered["new_submission"]
        runtime = make_runtime(tmp_path)
        try:
            records = await runtime.get_task_submissions(task.id)
            assert len(records) == 1 and records[0]["id"] == record["id"]
            assert records[0]["payload"] == record["payload"]
            record = records[0]
        finally:
            await runtime.stop()
    assert (await observe_submission(record))["observation"] == "running"
    worker_args = ([sys.executable, "-m", "Sprout.tests.commit_worker_signal_harness",
        "orchestrator", "task-worker", "--config", str(config_path), "--host", host,
        "--namespace", namespace, "--task-queue", queue] if worker_mode != "fixture"
        else [*prefix, "worker", str(route)])
    worker = await asyncio.create_subprocess_exec(*worker_args,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    try:
        ready = (await asyncio.wait_for(worker.stdout.readline(), timeout=20)).strip()
        if worker_mode != "fixture":
            assert json.loads(ready)["event"] == "task_worker_ready"
        else:
            assert ready == b"ready"
        observed = await child("observe")
        if live:
            async with asyncio.timeout(180):
                while observed["observation"] not in {"workflow_result", "persisted_result"}:
                    assert worker.returncode is None
                    observed = await child("observe")
        if code_policy:
            synced = await child("sync")
            report = await child("report")
            assert observed["result"]["status"] in {"waiting_approval", "waiting_resource"}
            if not live:
                assert observed["result"]["node_id"] == task.id + ":test_design"
                assert any("Acceptance design must contain a cases array" in
                           (node.get("error") or "") for node in report["execution"]["nodes"])
            else:
                assert report["budget"]["configured_limit"] == 5
                assert report["budget"]["consumed"] <= 5
            assert synced["task_status"] != "completed"
            assert (root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
            approval_process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "Sprout.cli.app", "approvals", "list", "--json",
                "--config", str(config_path), stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(approval_process.communicate(), timeout=20)
                assert approval_process.returncode == 0, err.decode(errors="replace")
                approvals = json.loads(out)
                if not live:
                    assert approvals == []
            finally:
                if approval_process.returncode is None:
                    approval_process.kill()
                    await approval_process.wait()
            (tmp_path / "production-policy-evidence.json").write_text(json.dumps({
                "observed": observed, "synced": synced, "report": report,
                "approvals": approvals,
                "paid_model_requests": report["budget"]["consumed"] if live else 0,
                "scope": "normal policy and formal CLI/Worker; no approval decisions",
                "model": "deepseek-flash" if live else "echo",
            }, indent=2), encoding="utf-8")
            return
        assert observed["result"]["status"] == "completed"
        assert worker.returncode is None
        synced = await child("sync")
        assert synced["synchronized"] and synced["task_status"] == "completed"
        repeated = await child("sync")
        assert not repeated["task_status_updated"]
        duplicate = await child("submit")
        assert not duplicate["new_submission"]
        report = await child("report")
        assert report["task_status"] == "completed"
        assert len(report["execution"]["nodes"]) == 2
        assert all(node["execution_state"] == "completed"
                   for node in report["execution"]["nodes"])
        assert worker.returncode is None and worker.pid not in pids
        client = await Client.connect(host, namespace=namespace)
        history = await client.get_workflow_handle(
            record["id"], run_id=record["run_id"]).fetch_history(rpc_timeout=timedelta(seconds=10))
        activity_count = sum(event.HasField("activity_task_scheduled_event_attributes")
                             for event in history.events)
        assert activity_count == 2
        assert (root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
        (tmp_path / "gateway-acceptance.json").write_text(json.dumps({
            "submitted": submitted, "observed": observed, "synced": synced,
            "report": report, "client_pids": pids, "worker_pid": worker.pid,
            "worker_survived_client_exit": True, "paid_model_requests": 0,
            "scheduled_activity_count": activity_count,
            "worker_mode": worker_mode,
            "scope": "real Temporal/SQLite/Runtime/activity; offline echo; read-only task",
        }, indent=2), encoding="utf-8")
    finally:
        if worker.returncode is None:
            worker.stdin.write(b"interrupt\n" if worker_mode != "fixture" else b"stop\n")
            await worker.stdin.drain()
        try:
            _, stderr = await asyncio.wait_for(worker.communicate(), timeout=15)
            assert worker.returncode == 0, stderr.decode(errors="replace")
        finally:
            if worker.returncode is None:
                worker.kill()
                await worker.wait()
