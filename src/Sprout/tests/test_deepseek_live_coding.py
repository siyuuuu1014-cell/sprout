"""Opt-in real model/AgentLoop/Temporal/Docker experiment, stopping at review.

Uses explicit experiment-only broker policy, not a production-policy acceptance.
Never collected into paid execution without SPROUT_DEEPSEEK_LIVE_CODING=1.
"""

import asyncio
import json
import os
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client

from Sprout.agent.executor import ActionExecutor
from Sprout.config.loader import load_env_file, load_settings
from Sprout.evolution.coding_profile import coding_profile
from Sprout.evolution.isolated_replay import git, prepare
from Sprout.evolution.regression_spec import freeze
from Sprout.evolution.replay_config import configuration_contract
from Sprout.evolution.replay_oracle import snapshot
from Sprout.evolution.replay_plan import build_plan
from Sprout.evolution.replay_snapshot import runtime_snapshot
from Sprout.execution.coding_dispatch import IsolatedNodeDispatcher
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.process_broker import ProcessBroker
from Sprout.gateway.local_identity import creator_record, current_cli_principal
from Sprout.orchestration.isolated_worker import IsolatedCodingWorker, experiment_task_queue
from Sprout.orchestration.models import NodeType
from Sprout.orchestration.terminal.temporal import TemporalConfig, TemporalOrchestratorBackend
from Sprout.runtime.factory import _build_provider, create_runtime
from Sprout.security.policy import SecurityPolicy
from Sprout.task.models import Task, TaskBudget, TaskStatus
from Sprout.tests.acceptance_samples import sample_definition
from Sprout.tests.live_coding_policy import LiveCodingPolicy
from Sprout.tests.live_model_budget import LiveModelBudget
from Sprout.tools.executor import ToolExecutor


async def test_real_deepseek_minimal_coding(tmp_path):
    if os.getenv("SPROUT_DEEPSEEK_LIVE_CODING") != "1":
        pytest.skip("Explicit paid experiment authorization required")
    # No implicit fresh allowance: operator must supply the remaining authorization.
    request_limit = int(os.environ["SPROUT_LIVE_REQUEST_LIMIT"])
    if not 1 <= request_limit <= 20:
        raise ValueError("Explicit remaining request limit must be 1..20")
    sample_name = os.environ.get("SPROUT_ACCEPTANCE_SAMPLE", "addition")
    sample = sample_definition(sample_name)
    host = os.environ["SPROUT_TEMPORAL_TEST_HOST"]
    image = os.environ["SPROUT_CODING_TEST_IMAGE"]
    assert host == "127.0.0.1:17233"
    load_env_file()
    configured = load_settings(os.environ.get("SPROUT_LIVE_CONFIG", "sprout.toml"))
    model_settings = replace(configured.model, max_output_tokens=8192)
    assert (model_settings.provider == "openai_compatible"
            and model_settings.model == "deepseek-flash"
            and model_settings.base_url.rstrip("/") == "https://api.deepseek.com"
            and model_settings.api_key_env == "DEEPSEEK_API_KEY"
            and model_settings.thinking_mode == "disabled")
    assert os.environ.get("DEEPSEEK_API_KEY", "").strip()
    model = LiveModelBudget(_build_provider(model_settings.provider, model_settings.model,
        model_settings.base_url, model_settings.api_key_env, model_settings), limit=request_limit)
    original = tmp_path / "original"
    original.mkdir()
    git(original, "init", "-q")
    # An explicit fixture baseline commit is not a model-authored commit.
    for name, content in sample["sources"].items():
        destination = original / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    (original / "oracles").mkdir()
    (original / "oracles/frozen.txt").write_text("host-only frozen oracle", encoding="utf-8")
    git(original, "add", ".")
    git(original, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", "explicit experiment baseline")
    # prepare() allocates all five experiment databases, not project databases.
    configured.model.provider = "echo"
    directory, repo, revision, settings = prepare(
        original, "HEAD", tmp_path / "experiments", configured)
    settings.model = model_settings
    runtime = create_runtime(settings)
    runtime._isolated_coding_required = True
    runtime._isolated_coding_image = image
    policy = LiveCodingPolicy()
    runtime._process_broker = ProcessBroker(policy, approvals=runtime.approvals)
    runtime._file_broker = FileBroker(policy)

    def reject_legacy(**kwargs):
        raise ValueError("Only isolated experiment execution is permitted")

    runtime._isolated_coding_dispatch = IsolatedNodeDispatcher(
        runtime.storage.metadata, runtime, reject_legacy)
    agent = runtime.agents.get(settings.runtime.default_agent)
    agent.model = model
    agent.executor = ActionExecutor(ToolExecutor(tools=runtime.tools, policy_engine=policy,
        approvals=runtime.approvals, policy=SecurityPolicy(
            always_allow=frozenset({"isolated_coding_command", "skill_view"}))))
    serving = None
    worker_process = None
    detached = os.getenv("SPROUT_LIVE_DETACHED") == "1"
    report = {"passed": False, "git_applied": False, "published": False,
              "sample": sample_name,
              "policy_scope": "explicit_experiment_fixture", "evidence_dir": str(directory)}
    try:
        if not detached:
            await runtime.start()
        workspace = await runtime.open_workspace(repo)
        principal = current_cli_principal()
        acceptance, regression = sample["acceptance"], sample["regression"]
        plan = build_plan(source_task_id="explicit-real-model-trial", revision=revision,
            mode="code", original=original, repo=repo, tests=("pytest",), regressions=("pytest",),
            oracle_paths=("oracles",), frozen=snapshot(repo, ("oracles",)), image=image,
            configuration=configuration_contract(settings), runtime=runtime_snapshot(runtime),
            creator=creator_record(principal), coding=coding_profile(image))
        task = Task(workspace_id=workspace.id, actor=principal, source="cli",
            status=TaskStatus.RUNNING,
            budget=TaskBudget(max_model_calls=request_limit),
            instruction=sample["instruction"],
            metadata={"execution_mode": "code", "replay_plan": plan,
                      "replay_source_task_id": "explicit-real-model-trial",
                      "acceptance_spec": acceptance, "require_acceptance_tests": True,
                      "required_paths": sample["required_paths"],
                      "verification_commands": sample["verification_commands"],
                      "regression_fingerprint": freeze(directory, regression)})
        await runtime.storage.metadata.save_task(task)
        report["task_id"] = task.id
        config = TemporalConfig(host=host, namespace="sprout-validation-" + uuid4().hex,
                                task_queue=experiment_task_queue(task))
        admin = await Client.connect(host)
        await admin.workflow_service.register_namespace(RegisterNamespaceRequest(
            namespace=config.namespace,
            workflow_execution_retention_period=Duration(seconds=86400)),
            timeout=timedelta(seconds=10))
        runtime._orchestrator_backend = TemporalOrchestratorBackend(
            config, metadata=runtime.storage.metadata, result_wait_seconds=240)
        if detached:
            async def invoke(command, *options):
                child = await asyncio.create_subprocess_exec(sys.executable, "-m",
                    "Sprout.tests.live_coding_cli_fixture", "evolution", command, task.id,
                    "--database", str(directory / "metadata.db"), *map(str, options),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL)
                try:
                    out, err = await asyncio.wait_for(child.communicate(), timeout=75)
                    assert child.returncode == 0, (out, err)
                    return json.loads(out)
                finally:
                    if child.returncode is None:
                        child.kill()
                        await child.wait()

            source_config = Path(os.environ["SPROUT_LIVE_CONFIG"]).resolve()
            submitted = await invoke("coding-submit", "--config", source_config,
                "--temporal-host", host, "--temporal-namespace", config.namespace)
            assert submitted["metrics"]["submission_state"] == "accepted"
            worker_process = await asyncio.create_subprocess_exec(sys.executable, "-m",
                "Sprout.tests.live_coding_cli_fixture", "evolution", "coding-worker", task.id,
                "--database", str(directory / "metadata.db"), "--config", str(source_config),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            async with asyncio.timeout(300):
                while True:
                    observed = await invoke("coding-status", "--wait-seconds", "30")
                    if observed["observation"] in {"workflow_result", "persisted_result"}:
                        break
                    assert worker_process.returncode is None, "Worker exited before result"
            synced = await invoke("coding-sync")
            result = await runtime.storage.metadata.get_task(task.id)
            report.update(detached=True, worker_pid=worker_process.pid,
                          synchronization=synced)
        else:
            serving = asyncio.create_task(IsolatedCodingWorker(runtime, task, config).run())
            result = await asyncio.wait_for(runtime.execute(task), timeout=270)
        nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
        evaluations = [node for node in nodes if node.type == NodeType.EVALUATION]
        proposals = await runtime.list_change_proposals(task.id)
        report.update(task_status=result.status.value,
            evaluation_verified=any(node.metadata.get("output", {}).get(
                "verification_report", {}).get("verified") is True for node in evaluations),
            proposals=[{"id": proposal.id, "status": proposal.status.value,
                        "files_changed": list(proposal.files_changed)} for proposal in proposals],
            original_unchanged=git(original, "rev-parse", "HEAD") == revision
                and not git(original, "status", "--porcelain"))
        report["passed"] = bool(report["evaluation_verified"] and proposals
                                and report["original_unchanged"])
        assert report["passed"], "Real model task did not reach verified code-review boundary"
    finally:
        if worker_process is not None and worker_process.returncode is None:
            worker_process.stdin.write(b"interrupt\n")
            await worker_process.stdin.drain()
            try:
                await asyncio.wait_for(worker_process.communicate(), timeout=20)
            finally:
                if worker_process.returncode is None:
                    worker_process.kill()
                    await worker_process.wait()
            report["worker_exit_code"] = worker_process.returncode
        if detached and report.get("task_id"):
            budget_report = await runtime.storage.metadata.model_budget_report(
                task.id, request_limit)
            report.update(requests_attempted=budget_report["consumed"], request_limit=request_limit,
                          prompt_tokens=budget_report.get("known_input_tokens"),
                          completion_tokens=budget_report.get("known_output_tokens"),
                          persistent_budget=budget_report, money_hard_cap=False)
        else:
            report.update(model.summary())
        (tmp_path / "live-coding-report.json").write_text(json.dumps(report, indent=2),
                                                         encoding="utf-8")
        if serving is not None:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
        await runtime.stop()
        await runtime.storage.close()
