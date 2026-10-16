"""Opt-in real Runtime/AgentLoop/Temporal/Docker; scripted model and test policy."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.worker import Replayer

from Sprout.agent.executor import ActionExecutor
from Sprout.config.loader import default_settings
from Sprout.evolution.coding_profile import coding_profile
from Sprout.evolution.isolated_replay import content_digest, git, prepare
from Sprout.evolution.regression_spec import freeze
from Sprout.evolution.replay_config import configuration_contract
from Sprout.evolution.replay_oracle import snapshot
from Sprout.evolution.replay_plan import build_plan
from Sprout.evolution.replay_snapshot import runtime_snapshot
from Sprout.execution.coding_dispatch import IsolatedNodeDispatcher
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.process_broker import ProcessBroker
from Sprout.gateway.local_identity import creator_record, current_cli_principal
from Sprout.llm.messages import LLMResponse, Role, ToolCall
from Sprout.orchestration.isolated_worker import IsolatedCodingWorker, experiment_task_queue
from Sprout.orchestration.models import NodeStatus, NodeType
from Sprout.orchestration.temporal_workflows import SproutExecutionWorkflow
from Sprout.orchestration.terminal.temporal import TemporalConfig, TemporalOrchestratorBackend
from Sprout.runtime.factory import create_runtime
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.security.policy import SecurityPolicy
from Sprout.skills.models import Skill
from Sprout.skills.repository import SkillRepository
from Sprout.task.models import Task, TaskStatus
from Sprout.tests.test_coding_docker_integration import (
    FixturePolicy,
    inspect_and_remove_owned_stopped,
)
from Sprout.tools.executor import ToolExecutor


class ScriptedAdditionModel:
    name = "explicit-scripted-addition-fixture"
    model = name

    def __init__(self, skill_name=None, skill_body=None):
        self.calls = 0
        self.commands = 0
        self.skill_name, self.skill_body = skill_name, skill_body
        self.skill_requested = False
        self.skill_read = False

    async def chat(self, messages, *, tools=()):
        self.calls += 1
        if "isolated_coding_command" in {tool.name for tool in tools} and not self.commands:
            if self.skill_name and not self.skill_requested:
                index = "\n".join(m.content or "" for m in messages if m.role == Role.SYSTEM)
                assert self.skill_name in index
                assert "skill_view" in {tool.name for tool in tools}
                self.skill_requested = True
                return LLMResponse(tool_calls=(ToolCall("read-coding-procedure", "skill_view",
                    {"name": self.skill_name}),), finish_reason="tool_calls")
            if self.skill_name:
                bodies = [m.content or "" for m in messages
                          if m.role == Role.TOOL and m.name == "skill_view"]
                assert len(bodies) == 1 and self.skill_body in bodies[0], bodies
                self.skill_read = True
            self.commands += 1
            script = ("from pathlib import Path; "
                      "Path('app.py').write_text('def add(a,b): return a+b\\n')")
            return LLMResponse(tool_calls=(ToolCall("fixed-command", "isolated_coding_command",
                {"command": ["python", "-c", script]}),), finish_reason="tool_calls")
        return LLMResponse(content="Fixed fixture plan/summary; no external model",
                           finish_reason="stop")


class CodingFixturePolicy(FixturePolicy):
    require_image_approval = False

    def decide(self, request):
        if (self.require_image_approval and request.action == ActionType.PROCESS_RUN
                and tuple(request.arguments.get("command", ()))[:3]
                == ("docker", "image", "inspect")):
            return PolicyDecision(AccessDecision.REQUIRE_APPROVAL,
                                  "explicit fixture process approval")
        if (request.action == ActionType.TOOL_INVOKE
                and request.arguments.get("tool_name")
                in {"isolated_coding_command", "skill_view"}):
            return PolicyDecision(AccessDecision.ALLOW, "explicit opt-in tool fixture")
        return super().decide(request)


@pytest.mark.parametrize("require_tool_approval,require_image_approval,with_skill",
                         [(False, False, False), (True, False, False), (False, True, False),
                          (False, False, True)])
async def test_full_runtime_coding_graph_waits_for_approval(
    tmp_path, monkeypatch, require_tool_approval, require_image_approval, with_skill,
    published_skill=None, coding_sink=None,
):
    host, image = os.getenv("SPROUT_TEMPORAL_TEST_HOST"), os.getenv("SPROUT_CODING_TEST_IMAGE")
    if not host or not image:
        pytest.skip("Explicit isolated Temporal endpoint and pinned coding image required")
    assert host == "127.0.0.1:17233"
    original = tmp_path / "original"
    original.mkdir()
    git(original, "init", "-q")
    (original / "app.py").write_text("def add(a,b): return 0\n", encoding="utf-8")
    (original / "test_smoke.py").write_text(
        "import importlib.util\nfrom pathlib import Path\n"
        "def test_add():\n"
        "    spec=importlib.util.spec_from_file_location('app', Path('app.py'))\n"
        "    app=importlib.util.module_from_spec(spec); spec.loader.exec_module(app)\n"
        "    assert app.add(2,3)==5\n", encoding="utf-8")
    (original / "oracles").mkdir()
    (original / "oracles/frozen.txt").write_text("host-only oracle fixture", encoding="utf-8")
    git(original, "add", ".")
    git(original, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", "fixture baseline")
    settings = default_settings()
    settings.model.provider = "echo"
    directory, repo, revision, settings = prepare(
        original, "HEAD", tmp_path / "experiments", settings)
    skill_name = "published-addition-procedure"
    skill_body = ("Integer addition procedure.\n\n"
                  "Implement app.add with a + b; run the frozen checks.")
    if with_skill:
        # A published-file fixture. Actual candidate approval/publication is
        # independently covered by the paired measurement publication test.
        if published_skill is not None:
            loaded = SkillRepository(published_skill["skills_dir"]).load(published_skill["name"])
            assert loaded is not None
            assert content_digest(loaded.instructions) == published_skill["content_hash"]
            assert loaded.version == published_skill["version"]
            skill_name, skill_body = loaded.name, loaded.instructions
        else:
            loaded = Skill(name=skill_name, version="1", instructions=skill_body)
        SkillRepository(settings.skills_dir).save(loaded)
    runtime = create_runtime(settings)
    runtime._isolated_coding_required = True
    runtime._isolated_coding_image = image
    policy = CodingFixturePolicy()
    policy.require_image_approval = require_image_approval
    runtime._process_broker = ProcessBroker(policy, approvals=runtime.approvals)
    runtime._file_broker = FileBroker(policy)
    legacy_attempts = []

    def reject_legacy_mapping(**kwargs):
        legacy_attempts.append(kwargs)
        raise ValueError("Fixture only supports dedicated command/JSON/auxiliary sessions")

    runtime._isolated_coding_dispatch = IsolatedNodeDispatcher(
        runtime.storage.metadata, runtime, reject_legacy_mapping)
    model = ScriptedAdditionModel(skill_name if with_skill else None, skill_body)
    agent = runtime.agents.get(settings.runtime.default_agent)
    agent.model = model
    agent.executor = ActionExecutor(ToolExecutor(tools=runtime.tools, policy_engine=policy,
        approvals=runtime.approvals,
        policy=SecurityPolicy(always_allow=frozenset() if require_tool_approval
                              else frozenset({"isolated_coding_command", "skill_view"}))))
    serving = None
    commit_process = None
    await runtime.start()
    metadata = runtime.storage.metadata
    try:
        workspace = await runtime.open_workspace(repo)
        principal = current_cli_principal()
        acceptance = {"cases": [{"id": "normal", "target": "app:add", "args": [2, 3],
                                  "expected_output": 5}]}
        regression = {"cases": [{"id": "zero", "target": "app:add", "args": [-1, 1],
                                  "expected_output": 0}]}
        plan = build_plan(source_task_id="explicit-full-graph-fixture", revision=revision,
            mode="code", original=original, repo=repo, tests=("pytest",), regressions=("pytest",),
            oracle_paths=("oracles",), frozen=snapshot(repo, ("oracles",)), image=image,
            configuration=configuration_contract(settings), runtime=runtime_snapshot(runtime),
            creator=creator_record(principal), coding=coding_profile(image))
        task = Task(workspace_id=workspace.id, actor=principal, source="cli",
            status=TaskStatus.RUNNING, instruction="Implement integer addition in app.py",
            metadata={"execution_mode": "code", "replay_plan": plan,
                      "replay_source_task_id": "explicit-full-graph-fixture",
                      "acceptance_spec": acceptance, "require_acceptance_tests": True,
                      "required_paths": ["app.py"],
                      "regression_fingerprint": freeze(directory, regression)})
        await metadata.save_task(task)
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
        runtime._orchestrator_backend = TemporalOrchestratorBackend(config, metadata=metadata)
        serving = asyncio.create_task(worker.run())
        result = await asyncio.wait_for(runtime.execute(task), timeout=45)
        assert result.status == TaskStatus.WAITING_APPROVAL, result
        if require_tool_approval or require_image_approval:
            initial_nodes = await metadata.list_execution_nodes(task.id)
            assert not any(node.type == NodeType.EVALUATION for node in initial_nodes)
            parked_type = NodeType.AGENT if require_tool_approval else NodeType.TEST_DESIGN
            parked = next(node for node in initial_nodes if node.type == parked_type)
            slot = "evaluation-0" if require_tool_approval else "baseline-acceptance-0"
            identity = str(uuid5(NAMESPACE_URL, parked.id + ":coding:" + slot))
            assert await metadata.get_coding_execution(identity) is None
            approval_tool = "isolated_coding_command" if require_tool_approval else "process_run"
            grants = [grant for grant in await runtime.pending_approvals()
                      if grant.tool == approval_tool and grant.task_id == task.id]
            assert len(grants) == 1
            grant = grants[0]
            assert grant.source == "cli" and grant.requested_by == principal.user_id
            assert grant.resource_scope == workspace.id
            await runtime.approvals.decide(grant.id, True, decided_by=principal.user_id,
                channel="cli", reason="Explicit fixture approval; no Git or production grant")
            result = await asyncio.wait_for(runtime.resume_task(task), timeout=45)
            assert result.status == TaskStatus.WAITING_APPROVAL
            for previous in initial_nodes:
                if previous.status == NodeStatus.COMPLETED:
                    assert await metadata.get_execution_node(previous.id) == previous
        assert (await metadata.get_task(task.id)).status == TaskStatus.WAITING_APPROVAL
        nodes = await metadata.list_execution_nodes(task.id)
        evaluation = next(node for node in nodes if node.type == NodeType.EVALUATION
                          and node.metadata.get("output", {}).get("status") == "passed")
        assert evaluation.metadata["output"]["verification_report"]["verified"] is True
        assert any("pytest" in item["name"]
                   for item in evaluation.metadata["output"]["test_results"])
        assert not any(node.type == NodeType.APPLY for node in nodes)
        proposals = await runtime.list_change_proposals(task.id)
        assert len(proposals) == 1 and proposals[0].status.value == "pending"
        assert proposals[0].files_changed == ("app.py",)
        assert model.commands == 1
        calls = model.calls
        records = await metadata.list_task_submissions(task.id)
        assert len(records) == (2 if require_tool_approval or require_image_approval else 1)
        for index, record in enumerate(records):
            history = await client.get_workflow_handle(record["id"]).fetch_history(
                rpc_timeout=timedelta(seconds=10))
            (directory / f"history-{index}.json").write_text(history.to_json(), encoding="utf-8")
            replay = await asyncio.wait_for(
                Replayer(workflows=[SproutExecutionWorkflow]).replay_workflow(history), timeout=20)
            assert replay.replay_failure is None
        repeated = await runtime.execute(task)
        assert repeated.status == TaskStatus.WAITING_APPROVAL
        assert await metadata.list_execution_nodes(task.id) == nodes
        assert await metadata.list_task_submissions(task.id) == records
        assert model.calls == calls and model.commands == 1
        assert len(await runtime.list_change_proposals(task.id)) == 1
        assert git(repo, "rev-parse", "HEAD") == git(original, "rev-parse", "HEAD") == revision
        assert (repo / "app.py").read_text() == (original / "app.py").read_text()
        assert legacy_attempts == []
        assert model.skill_read is with_skill
        if not require_tool_approval and not require_image_approval:
            from dataclasses import replace

            from typer.testing import CliRunner

            from Sprout.cli.commands.evolution import app as evolution_app
            from Sprout.execution.git_transaction_contract import commit_write_contract, fingerprint
            from Sprout.execution.isolated_git_coordinator import _admit
            from Sprout.execution.isolated_git_plan import IsolatedGitPlan
            from Sprout.execution.models import ChangeProposalStatus
            from Sprout.orchestration.commit_workflows import SproutExperimentCommitWorkflow
            from Sprout.orchestration.isolated_worker import commit_task_queue
            from Sprout.runtime.experiment_commits import ExperimentCommitService
            from Sprout.storage.contracts.commit_request import commit_workflow_id

            # Only initial expired fixture creation needs the test clock and
            # injected settings; later commands and worker load real TOML in
            # independent processes, without admission/storage substitutions.
            monkeypatch.setattr("Sprout.config.loader.load_settings", lambda path=None: settings)
            config_path = directory / "fixture-settings.toml"
            config_path.write_text('[model]\nprovider = "echo"\n', encoding="utf-8")
            subprocess_commands = []
            use_process = False

            async def command(name, identity, *options):
                args = [name, task.id, identity, "--database", str(directory / "metadata.db"),
                        *options]
                if use_process:
                    if name in {"commit-request", "commit-refresh"}:
                        args += ["--config", str(config_path)]
                    process = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "Sprout", "evolution", *args,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    try:
                        out, err = await asyncio.wait_for(process.communicate(), timeout=30)
                    finally:
                        if process.returncode is None:
                            process.kill()
                            await process.wait()
                    assert process.returncode == 0, (out, err)
                    subprocess_commands.append(name)
                    return json.loads(out)
                response = await asyncio.to_thread(CliRunner().invoke, evolution_app, args)
                assert response.exit_code == 0, (response.output, response.exception)
                return json.loads(response.output)

            pending_snapshot = await metadata.get_git_proposal_snapshot(proposals[0].id)
            pending_review = await ExperimentCommitService(runtime).review(task.id, proposals[0].id,
                message="Explicit isolated fixture commit", author_name="Fixture Operator",
                author_email="fixture@invalid.example")
            assert pending_review["proposal_status"] == "pending"
            assert pending_review["write_authorized"] is False
            assert pending_review["evaluation_node_id"] == evaluation.id
            assert pending_review["files_changed"] == ["app.py"]
            assert await metadata.get_git_proposal_snapshot(proposals[0].id) == pending_snapshot

            proposal = replace(proposals[0], status=ChangeProposalStatus.APPROVED)
            await metadata.save_change_proposal(proposal)
            await metadata.update_task_status(task.id, TaskStatus.RUNNING)
            class Earlier(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.now(UTC) - timedelta(hours=2)

            # Create a naturally expired fixture using a test clock, without
            # editing persisted request contents or changing authority rules.
            with monkeypatch.context() as clock:
                clock.setattr("Sprout.runtime.experiment_commits.datetime", Earlier)
                created = await command("commit-request", proposal.id,
                    "--message", "Explicit isolated fixture commit",
                    "--author-name", "Fixture Operator",
                    "--author-email", "fixture@invalid.example")
            inbox = await runtime.storage.operational.get_commit_request(created["request_id"])
            assert inbox["state"] == "pending"
            # Only the expired fixture creation needs the parent test clock;
            # every following short command is a real independent CLI process.
            use_process = True
            repeated = await command("commit-request", proposal.id,
                "--message", "Explicit isolated fixture commit",
                "--author-name", "Fixture Operator", "--author-email", "fixture@invalid.example")
            assert repeated == created
            refreshed = await command("commit-refresh", inbox["id"],
                                      "--expected-fingerprint", inbox["fingerprint"])
            assert refreshed["supersedes"] == inbox["id"]
            inbox = await runtime.storage.operational.get_commit_request(refreshed["request_id"])
            assert inbox["state"] == "pending" and inbox["decision"] is None
            displayed = await command("commit-review", inbox["id"])
            assert displayed["requests"][0]["decision_fingerprint"] == inbox["fingerprint"]
            await command("commit-decide", inbox["id"], "--decision", "approve",
                "--expected-fingerprint", inbox["fingerprint"], "--reason", "Explicit CLI fixture")
            persisted_inbox = await runtime.storage.operational.get_commit_request(inbox["id"])
            assert persisted_inbox["dispatch_state"] == "waiting_schedule"
            assert git(repo, "rev-parse", "HEAD") == revision
            planner = IsolatedGitPlan(workspace, original=original, evidence=directory)
            commit_plan = await planner.commit_plan(proposal.sandbox_ref.root)
            contract = commit_write_contract(commit_plan, author_name="Fixture Operator",
                author_email="fixture@invalid.example", message="Explicit isolated fixture commit")
            source_file = Path(proposal.sandbox_ref.root) / "app.py"
            reviewed_source = source_file.read_text(encoding="utf-8")
            source_file.write_text(reviewed_source + "# unreviewed drift\n",
                                   encoding="utf-8", newline="")
            try:
                with pytest.raises(ValueError, match="changed"):
                    await _admit(runtime, task.id, proposal.id, evaluation.id, contract)
            finally:
                source_file.write_text(reviewed_source, encoding="utf-8", newline="")
            dirty = repo / "unrelated.txt"
            dirty.write_text("unrelated fixture change", encoding="utf-8")
            try:
                with pytest.raises(ValueError, match="unrelated"):
                    await _admit(runtime, task.id, proposal.id, evaluation.id, contract)
            finally:
                dirty.unlink()
            assert git(repo, "rev-parse", "HEAD") == revision
            # Real admission: no replacements for Runtime, proposal or raw Docker evidence.
            admitted = await _admit(runtime, task.id, proposal.id, evaluation.id, contract)
            arguments = admitted[-1]
            assert arguments == inbox["review"]["approval_arguments"]
            # Explicit persisted decision resumes only the approval wait, not
            # cancelled/paused tasks, without needing a second user command.
            await metadata.update_task_status(task.id, TaskStatus.WAITING_APPROVAL)
            commit_config = TemporalConfig(host=config.host, namespace=config.namespace,
                task_queue=commit_task_queue(task, inbox["review"]["routing_profile"]))
            commit_process = await asyncio.create_subprocess_exec(sys.executable,
                "-m", "Sprout.tests.commit_worker_signal_harness", "evolution", "commit-worker",
                task.id, inbox["id"], "--database", str(directory / "metadata.db"),
                "--config", str(config_path), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            dispatch = {"request_id": inbox["id"], "request_fingerprint": inbox["fingerprint"],
                "workflow_id": commit_workflow_id(inbox["id"], inbox["fingerprint"]),
                "host": commit_config.host, "namespace": commit_config.namespace,
                "task_queue": commit_config.task_queue}
            await runtime.storage.operational.ensure_commit_dispatch(dispatch)
            # Emulate a lost start acknowledgement: the server already owns
            # the exact workflow, while the local dispatch remains pending.
            handle = await client.start_workflow(SproutExperimentCommitWorkflow.run,
                {"request_id": inbox["id"], "request_fingerprint": inbox["fingerprint"]},
                id=dispatch["workflow_id"], task_queue=commit_config.task_queue,
                memo={"sprout_commit_dispatch": fingerprint(dispatch)})
            delivery = await command("commit-submit", inbox["id"])
            assert delivery["dispatch_state"] == "accepted", delivery
            committed = await asyncio.wait_for(handle.result(), timeout=45)
            if not committed["applied"]:
                commit_process.stdin.write(b"interrupt\n")
                await commit_process.stdin.drain()
                out, err = await asyncio.wait_for(commit_process.communicate(), timeout=30)
                pytest.fail(repr((committed, out, err)))
            assert committed["validation_committed"] is False
            assert committed["task_completed"] is True, committed
            finalized = await command("commit-finalize", inbox["id"])
            assert finalized["task_completed"] is True and finalized["git_executed"] is False
            commit_history = await handle.fetch_history(rpc_timeout=timedelta(seconds=10))
            replayed = await Replayer(workflows=[SproutExperimentCommitWorkflow]).replay_workflow(
                commit_history)
            assert replayed.replay_failure is None
            schedules = [event.activity_task_scheduled_event_attributes
                for event in commit_history.events
                if event.HasField("activity_task_scheduled_event_attributes")]
            assert len(schedules) == 1 and schedules[0].retry_policy.maximum_attempts == 1
            assert git(original, "rev-parse", "HEAD") == revision
            assert (original / "app.py").read_text() == "def add(a,b): return 0\n"
            assert git(repo, "rev-parse", "HEAD") == committed["result_commit"]
            assert (repo / "app.py").read_text() == "def add(a,b): return a+b\n"
            assert (await metadata.get_git_transaction(committed["transaction_id"]))[
                "state"] == "completed"
            assert (await metadata.get_task(task.id)).status == TaskStatus.COMPLETED
            settled_nodes = await metadata.list_execution_nodes(task.id)
            assert all(n.status in {NodeStatus.COMPLETED, NodeStatus.SKIPPED}
                       for n in settled_nodes)
            assert (await metadata.get_execution_node(task.id + ":approval")).status == (
                NodeStatus.COMPLETED)
            assert (await metadata.get_execution_node(task.id + ":apply")).status == (
                NodeStatus.COMPLETED)
            assert model.calls == calls and model.commands == 1
            # Deliver SIGINT in the child, exercising CLI interrupt handling
            # and owned SDK shutdown before independently reading the result.
            commit_process.stdin.write(b"interrupt\n")
            await commit_process.stdin.drain()
            out, err = await asyncio.wait_for(commit_process.communicate(), timeout=30)
            assert commit_process.returncode == 0, (out, err)
            assert b"Commit worker stopped" in out
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "Sprout",
                "evolution", "commit-status", task.id, "--database", str(directory / "metadata.db"),
                "--request-id", inbox["id"], stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(process.communicate(), timeout=30)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            assert process.returncode == 0, (out, err)
            status_report = json.loads(out)
            assert status_report["requests"][0]["status"] == "applied"
            assert status_report["requests"][0]["task_completed"] is True
            assert status_report["requests"][0]["result_commit"] == committed["result_commit"]
            assert status_report["write_authorized"] is False
            assert subprocess_commands == ["commit-request", "commit-refresh", "commit-review",
                                           "commit-decide", "commit-submit", "commit-finalize"]
            if coding_sink is not None:
                coding_sink.update(task_id=task.id, evidence_dir=str(directory),
                    skill_read=model.skill_read, skill_name=skill_name,
                    skill_content_hash=content_digest(skill_body), model_calls=model.calls,
                    command_count=model.commands, verified=True,
                    result_commit=committed["result_commit"], applied=True,
                    task_status=(await metadata.get_task(task.id)).status.value)
        (directory / "temporal-coding-smoke.json").write_text(json.dumps({
            "task_id": task.id, "status": (await metadata.get_task(task.id)).status.value,
            "initial_workflow_status": result.status.value, "model_calls": calls,
            "commands": model.commands, "verified": True,
            "coding_skill_read": model.skill_read,
            "real_tool_approval": require_tool_approval,
            "real_process_approval": require_image_approval,
            "real_isolated_git_commit": not require_tool_approval and not require_image_approval,
            "scope": "real Runtime/AgentLoop/Temporal/Docker; scripted model and test-only policy; "
                     "no production approval or growth validation proof"}, indent=2),
                     encoding="utf-8")
    finally:
        if commit_process is not None and commit_process.returncode is None:
            # Only this test's exact child; forced cleanup is not a graceful
            # shutdown assertion and cannot make the preceding checks pass.
            commit_process.kill()
            await commit_process.wait()
        if serving is not None:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
        # Exact recorded commands only; never remove active/unknown containers.
        try:
            for node in await metadata.list_execution_nodes(task.id) if "task" in locals() else []:
                known_slots = {NodeType.TEST_DESIGN: ("baseline-acceptance-0",),
                    NodeType.AGENT: ("evaluation-0",),
                    NodeType.EVALUATION: ("auxiliary-0", "evaluation-acceptance-0",
                                         "evaluation-regression-0", "evaluation-poc-0")}
                for slot in known_slots.get(node.type, ()):
                    identity = str(uuid5(NAMESPACE_URL, node.id + ":coding:" + slot))
                    record = await metadata.get_coding_execution(identity)
                    if record and record["state"] == "completed":
                        command = record["command"]
                        inspect_and_remove_owned_stopped(
                            command[command.index("--name") + 1], directory)
        finally:
            await runtime.stop()
