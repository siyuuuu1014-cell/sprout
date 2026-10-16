"""Opt-in separate-process CLI closure with offline fixture model/authorization."""

import asyncio
import json
import os
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client

from Sprout.artifacts.models import Artifact, ArtifactStatus
from Sprout.config.loader import load_settings
from Sprout.evolution.isolated_replay import git
from Sprout.execution.acceptance import render_acceptance_tests
from Sprout.execution.models import ChangeProposalStatus
from Sprout.orchestration.models import NodeStatus, NodeType
from Sprout.runtime.factory import create_runtime
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.tests.offline_evolution_cli_harness import CANDIDATE_BODY, CANDIDATE_NAME
from Sprout.tests.test_coding_docker_integration import inspect_and_remove_owned_stopped
from Sprout.tests.test_replay_resume_docker_integration import clean_owned_containers


async def test_independent_cli_processes_complete_and_validate_both_experiments(tmp_path):
    host = os.getenv("SPROUT_TEMPORAL_TEST_HOST")
    coding_image = os.getenv("SPROUT_CODING_TEST_IMAGE")
    verification_image = os.getenv("SPROUT_VERIFICATION_TEST_IMAGE")
    if not all((host, coding_image, verification_image)):
        pytest.skip("Explicit dedicated Temporal and two pinned local images required")
    assert host == "127.0.0.1:17233"
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"TEMPORAL_HOST", "TEMPORAL_NAMESPACE", "TEMPORAL_TASK_QUEUE"}}
    namespace = "sprout-validation-" + uuid4().hex
    admin = await Client.connect(host)
    await admin.workflow_service.register_namespace(RegisterNamespaceRequest(
        namespace=namespace, workflow_execution_retention_period=Duration(seconds=86400)),
        timeout=timedelta(seconds=10))
    client = await Client.connect(host, namespace=namespace)
    authority = tmp_path / "authority"
    authority.mkdir()
    storage = {slot: "sqlite:///" + (authority / (slot + ".db")).as_posix()
               for slot in ("metadata", "operational", "session", "context", "usage")}
    storage.update(knowledge="memory://", cache="memory", vectors="memory", graph="none",
                   trajectory_dir=str(authority / "trajectory"), blobs_dir=str(authority / "blobs"),
                   project_root=str(authority / "projects"))
    config = authority / "source.toml"
    config.write_text('skills_dir = ' + json.dumps(str(authority / "skills")) +
        '\n[model]\nprovider = "echo"\n[storage]\n' +
        "\n".join(key + " = " + json.dumps(value) for key, value in storage.items()) +
        '\n[storage.observations]\nenabled = false\n[memory]\nhome = ' +
        json.dumps(str(authority / "memory")) + '\n[security.audit]\npath = ' +
        json.dumps(str(authority / "audit.jsonl")) + "\n", encoding="utf-8")
    original = tmp_path / "original"
    original.mkdir()
    git(original, "init", "-q")
    acceptance = {"cases": [{"id": "normal", "target": "app:add", "args": [2, 3],
                              "expected_output": 5}]}
    (original / "app.py").write_text("def add(a,b): return 0\n", encoding="utf-8")
    (original / "oracles").mkdir()
    (original / "oracles/test_acceptance.py").write_text(
        render_acceptance_tests(AcceptanceSpec.from_payload(acceptance)),
        encoding="utf-8", newline="")
    (original / "test_smoke.py").write_text(
        "import importlib.util\nfrom pathlib import Path\n"
        "def test_add():\n"
        "    spec=importlib.util.spec_from_file_location('app', Path('app.py'))\n"
        "    app=importlib.util.module_from_spec(spec); spec.loader.exec_module(app)\n"
        "    assert app.add(2,3)==5\n", encoding="utf-8")
    git(original, "add", ".")
    git(original, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", "offline baseline")
    revision = git(original, "rev-parse", "HEAD")
    regression = tmp_path / "regression.json"
    regression.write_text(json.dumps({"cases": [{"id": "zero", "target": "app:add",
        "args": [-1, 1], "expected_output": 0}]}), encoding="utf-8")
    source = create_runtime(load_settings(str(config)))
    processes = []

    async def invoke(command, *args, fixture=False, with_config=False, timeout=90):
        module = "Sprout.tests.offline_evolution_cli_harness" if fixture else "Sprout"
        argv = [sys.executable, "-m", module, "evolution", command, *map(str, args)]
        if with_config:
            argv += ["--config", str(config)]
        child = await asyncio.create_subprocess_exec(*argv, env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(child.communicate(), timeout=timeout)
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
        processes.append({"pid": child.pid, "command": command, "exit_code": child.returncode,
                          "offline_adapter": fixture})
        assert child.returncode == 0, (command, out, err)
        assert child.pid != os.getpid()
        return json.loads(out)

    async def stop_worker(child):
        try:
            if child.returncode is None:
                child.stdin.write(b"interrupt\n")
                await child.stdin.drain()
            out, err = await asyncio.wait_for(child.communicate(), timeout=30)
            assert child.returncode == 0, (out, err)
            assert b"worker stopped" in out, (out, err)
            processes.append({"pid": child.pid, "command": "commit-worker",
                              "exit_code": child.returncode, "offline_adapter": False})
        finally:
            if child.returncode is None:
                child.kill()  # Exact test child only; forced cleanup is not a passing shutdown.
                await child.wait()

    experiments = []
    try:
        workspace = await source.open_workspace(original)
        task = await source.create_task(workspace.id, "Implement integer addition in app.py",
            execution_mode="code", metadata={"required_paths": ["app.py"],
                "require_acceptance_tests": True, "acceptance_spec": acceptance})
        artifact = Artifact(name=CANDIDATE_NAME, content=CANDIDATE_BODY,
                            status=ArtifactStatus.CANDIDATE, evidence_ids=("a", "b", "c"))
        await source.storage.metadata.save_artifact(artifact)
        compared = await invoke("replay-compare", artifact.id, task.id, "--revision", revision,
            "--output-dir", tmp_path / "experiments", "--check", "pytest", "--regression", "pytest",
            "--oracle", "oracles", "--regression-spec", regression,
            "--verification-image", verification_image, "--coding-image", coding_image,
            "--temporal-host", host, "--temporal-namespace", namespace,
            fixture=True, with_config=True, timeout=120)
        pair = Path(compared["pair_manifest"])
        old_pair = pair.read_bytes()
        for role in ("baseline", "candidate"):
            experiment = compared[role]
            experiments.append(experiment)
            identity, directory = experiment["task_id"], Path(experiment["evidence_dir"])
            assert experiment["status"] == "waiting_approval", experiment
            database = directory / "metadata.db"
            metadata = open_metadata_store(str(database))
            try:
                before = await metadata.list_execution_nodes(identity)
                pending = await invoke("replay-continue", identity, "--database", database,
                                       fixture=True, with_config=True)
                assert len(pending["pending_approvals"]) == 1, pending
                await invoke("replay-continue", identity, "--database", database, "--approve",
                    pending["pending_approvals"][0]["approval_id"], fixture=True, with_config=True)
                for node in before:
                    if node.status is NodeStatus.COMPLETED:
                        assert await metadata.get_execution_node(node.id) == node
                nodes = await metadata.list_execution_nodes(identity)
                evaluation = next(node for node in nodes if node.type is NodeType.EVALUATION)
                assert evaluation.metadata["output"]["verification_report"]["verified"] is True
                routed = await invoke("replay-continue", identity, "--database", database,
                                      fixture=True, with_config=True)
                proposal = await metadata.get_change_proposal(routed["proposal_id"])
                # Explicit fixture code-review decision, not a CLI approval claim.
                await metadata.save_change_proposal(replace(proposal,
                    status=ChangeProposalStatus.APPROVED))
                request = await invoke("commit-request", identity, proposal.id,
                    "--database", database,
                    "--message", "Explicit offline CLI experiment", "--author-name", "Fixture",
                    "--author-email", "fixture@invalid.example", with_config=True)
                request_id = request["request_id"]
                reviewed = await invoke("commit-review", identity, request_id,
                                        "--database", database)
                fingerprint = reviewed["requests"][0]["decision_fingerprint"]
                await invoke("commit-decide", identity, request_id, "--database", database,
                    "--decision", "approve", "--expected-fingerprint", fingerprint,
                    "--reason", "Explicit offline CLI decision")
                worker = await asyncio.create_subprocess_exec(sys.executable,
                    "-m", "Sprout.tests.commit_worker_signal_harness", "evolution", "commit-worker",
                    identity, request_id, "--database", str(database), "--config", str(config),
                    env=environment, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    submitted = await invoke("commit-submit", identity, request_id,
                                             "--database", database)
                    result = await asyncio.wait_for(client.get_workflow_handle(
                        submitted["workflow_id"]).result(), timeout=45)
                    assert result["applied"] and result["task_completed"], result
                finally:
                    await stop_worker(worker)
                status = await invoke("commit-status", identity, "--database", database,
                                      "--request-id", request_id)
                assert status["requests"][0]["task_completed"] is True
                finalized = await invoke("commit-finalize", identity, request_id,
                                         "--database", database)
                assert finalized["task_completed"] and not finalized["git_executed"]
                waiting_bytes = Path(experiment["measurement_path"]).read_bytes()
                measured = await invoke("replay-measure", identity, "--database", database,
                                        fixture=True, with_config=True)
                assert measured["measurement_status"] == "observed", measured
                completed_bytes = Path(measured["measurement_path"]).read_bytes()
                cached = await invoke("replay-measure", identity, "--database", database,
                                      fixture=True, with_config=True)
                assert cached["cached"] is True
                assert Path(measured["measurement_path"]).read_bytes() == completed_bytes
                assert Path(experiment["measurement_path"]).read_bytes() == waiting_bytes
            finally:
                metadata.close()
        refreshed = await invoke("comparison-refresh", pair)
        assert refreshed["report"]["validation_readiness"]["observation_gates_passed"], refreshed
        assert pair.read_bytes() == old_pair
        report = await invoke("comparison-report", refreshed["pair_manifest"])
        assert report["validation_readiness"]["observation_gates_passed"] is True
        validated = await invoke("candidate-validate", artifact.id,
                                 "--pair", refreshed["pair_manifest"], with_config=True)
        assert validated["status"] == "validated" and validated["published"] is False
        final_artifact = await source.storage.metadata.get_artifact(artifact.id)
        assert final_artifact.status is ArtifactStatus.VALIDATED
        assert not (authority / "skills" / (artifact.name + ".toml")).exists()
        assert (await source.get_task(task.id)).status == "created"
        assert not await source.storage.metadata.list_execution_nodes(task.id)
        assert git(original, "rev-parse", "HEAD") == revision
        assert git(original, "status", "--porcelain") == ""
        (tmp_path / "independent-cli-acceptance.json").write_text(json.dumps({
            "passed": True, "processes": processes, "original_pair": str(pair),
            "completed_pair": refreshed["pair_manifest"], "candidate_status": "validated",
            "published": False, "scope": "independent Python CLI processes; scripted model, "
                "fixture tool/process policy and explicit fixture code review; no production model "
                "or policy acceptance, no autonomous learning or improvement claim"}, indent=2),
                encoding="utf-8")
    finally:
        # Only exact completed fixture execution records, never global Docker enumeration.
        for experiment in experiments:
            directory = Path(experiment["evidence_dir"])
            metadata = open_metadata_store(str(directory / "metadata.db"))
            try:
                names = []
                identity = experiment["task_id"]
                for slot in (":verification:test-0", ":verification:regression-0",
                             ":function-acceptance:normal", ":function-regression:zero"):
                    record = await metadata.get_verification_execution(
                        str(uuid5(NAMESPACE_URL, identity + slot)))
                    if record and record["state"] == "completed":
                        names.append(record["container_name"])
                clean_owned_containers(names, directory)
                for node in await metadata.list_execution_nodes(identity):
                    for slot in ("baseline-acceptance-0", "evaluation-0", "auxiliary-0",
                                 "evaluation-acceptance-0", "evaluation-regression-0",
                                 "evaluation-poc-0"):
                        record = await metadata.get_coding_execution(
                            str(uuid5(NAMESPACE_URL, node.id + ":coding:" + slot)))
                        if record and record["state"] == "completed":
                            command = record["command"]
                            inspect_and_remove_owned_stopped(
                                command[command.index("--name") + 1], directory)
            finally:
                metadata.close()
        await source.storage.close()
