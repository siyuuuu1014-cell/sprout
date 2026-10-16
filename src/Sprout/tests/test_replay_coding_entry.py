"""Explicit ReplayRunner entry; production assembly with an offline test model."""

import asyncio
import os
from datetime import timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client

from Sprout.agent.executor import ActionExecutor
from Sprout.artifacts.models import Artifact, ArtifactStatus
from Sprout.cli.coding_continuation import continue_coding
from Sprout.evolution.isolated_replay import git
from Sprout.evolution.replay_runner import ReplayRunner
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.process_broker import ProcessBroker
from Sprout.gateway.local_identity import current_cli_principal
from Sprout.orchestration.models import NodeType
from Sprout.runtime.factory import create_runtime
from Sprout.security.access import ActionType
from Sprout.security.policy import SecurityPolicy
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.tests.test_coding_docker_integration import (
    FixturePolicy,
    inspect_and_remove_owned_stopped,
)
from Sprout.tests.test_isolated_coding_temporal_integration import (
    CodingFixturePolicy,
    ScriptedAdditionModel,
)
from Sprout.tests.test_temporal_integration import _isolated_settings
from Sprout.tools.executor import ToolExecutor
from Sprout.trajectory.models import ArtifactSnapshot


async def commit_and_measure(experiment, *, settings, host, namespace):
    from dataclasses import replace

    from Sprout.cli.commit_decision import save_cli_decision
    from Sprout.cli.commit_submission import submit_cli_commit
    from Sprout.cli.commit_worker import create_cli_commit_request, serve_commit_worker
    from Sprout.cli.completed_measurement import measure_completed
    from Sprout.execution.models import ChangeProposalStatus
    from Sprout.tests.test_replay_resume_docker_integration import clean_owned_containers

    directory = Path(experiment.evidence_dir)
    database = directory / "metadata.db"
    metadata = open_metadata_store(str(database))
    principal = current_cli_principal()
    worker = None
    try:
        proposal = (await metadata.list_change_proposals(experiment.task_id))[0]
        await metadata.save_change_proposal(replace(proposal, status=ChangeProposalStatus.APPROVED))
        request = await create_cli_commit_request(database, experiment.task_id, proposal.id,
            settings=settings, principal=principal, message="Explicit offline experiment commit",
            author_name="Fixture Operator", author_email="fixture@invalid.example")
        assert request["request_id"], request
        await save_cli_decision(database, experiment.task_id, request["request_id"],
            principal=principal, expected_fingerprint=request["decision_fingerprint"],
            approved=True, reason="Explicit offline fixture commit decision")
        worker = asyncio.create_task(serve_commit_worker(database, experiment.task_id,
            request["request_id"], settings=settings, principal=principal))
        submitted = await submit_cli_commit(database, experiment.task_id,
            request["request_id"], principal=principal)
        client = await Client.connect(host, namespace=namespace)
        committed = await asyncio.wait_for(client.get_workflow_handle(
            submitted["workflow_id"]).result(), timeout=45)
        assert committed["applied"] and committed["task_completed"], committed
        assert (await metadata.get_task(experiment.task_id)).status == "completed"
        measured = await measure_completed(database, experiment.task_id,
                                            settings=settings, principal=principal)
        assert measured["measurement_status"] == "observed", measured
        cached = await measure_completed(database, experiment.task_id,
                                          settings=settings, principal=principal)
        assert cached["cached"] is True
        # Only completed, exact role-labelled test containers may be removed.
        names = []
        for slot in (":verification:test-0", ":verification:regression-0",
                     ":function-acceptance:normal", ":function-regression:zero"):
            saved = await metadata.get_verification_execution(
                str(uuid5(NAMESPACE_URL, experiment.task_id + slot)))
            if saved and saved["state"] == "completed":
                names.append(saved["container_name"])
        clean_owned_containers(names, directory)
        for node in await metadata.list_execution_nodes(experiment.task_id):
            for slot in ("baseline-acceptance-0", "evaluation-0", "auxiliary-0",
                         "evaluation-acceptance-0", "evaluation-regression-0", "evaluation-poc-0"):
                saved = await metadata.get_coding_execution(
                    str(uuid5(NAMESPACE_URL, node.id + ":coding:" + slot)))
                if saved and saved["state"] == "completed":
                    command = saved["command"]
                    inspect_and_remove_owned_stopped(
                        command[command.index("--name") + 1], directory)
        return committed
    finally:
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        metadata.close()


@pytest.mark.parametrize("namespace", [None, "explicit-ns"])
async def test_partial_temporal_options_do_not_create_experiment(tmp_path, monkeypatch, namespace):
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    settings = _isolated_settings(tmp_path / "runtime")
    runtime = create_runtime(settings)
    try:
        source = tmp_path / "source"
        source.mkdir()
        git(source, "init", "-q")
        workspace = await runtime.open_workspace(source)
        task = await runtime.create_task(workspace.id, "Implement addition", execution_mode="code")
        output = tmp_path / "experiments"
        result = await ReplayRunner(runtime).run(task.id, revision="HEAD",
            settings=settings, output_dir=output, cli_principal=current_cli_principal(),
            verification_commands=("pytest",), regression_commands=("pytest",),
            coding_image="sha256:" + "a" * 64, temporal_host="127.0.0.1:17233",
            temporal_namespace=namespace)
        assert result.status == "waiting_resource" and not result.task_id
        assert "namespace" in result.error
        assert not output.exists()
    finally:
        await runtime.stop()


@pytest.mark.parametrize("with_candidate,approval_kind",
                         [(False, "none"), (True, "tool"), (True, "process")])
async def test_replay_runner_uses_dedicated_worker_without_generic_start(
    tmp_path, monkeypatch, with_candidate, approval_kind,
):
    host, image = os.getenv("SPROUT_TEMPORAL_TEST_HOST"), os.getenv("SPROUT_CODING_TEST_IMAGE")
    verification_image = os.getenv("SPROUT_VERIFICATION_TEST_IMAGE")
    if not host or not image or not verification_image:
        pytest.skip("Explicit test Temporal endpoint and local pinned coding image required")
    assert host == "127.0.0.1:17233"
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    namespace = "sprout-validation-" + uuid4().hex
    client = await asyncio.wait_for(Client.connect(host), timeout=15)
    await client.workflow_service.register_namespace(RegisterNamespaceRequest(
        namespace=namespace, workflow_execution_retention_period=Duration(seconds=86400)),
        timeout=timedelta(seconds=10))
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
    (original / "oracles/frozen.txt").write_text("host-only fixture", encoding="utf-8")
    from Sprout.execution.acceptance import render_acceptance_tests
    from Sprout.strategy.acceptance import AcceptanceSpec

    acceptance = {"cases": [{"id": "normal", "target": "app:add", "args": [2, 3],
                              "expected_output": 5}]}
    (original / "oracles/test_acceptance.py").write_text(
        render_acceptance_tests(AcceptanceSpec.from_payload(acceptance)),
        encoding="utf-8", newline="")
    git(original, "add", ".")
    git(original, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", "baseline")
    revision = git(original, "rev-parse", "HEAD")
    source_settings = _isolated_settings(tmp_path / "source-runtime")
    source = create_runtime(source_settings)
    candidate = Artifact(name="addition-procedure", status=ArtifactStatus.CANDIDATE,
        content="Implement app.add with a + b and run the frozen checks. " * 3,
        evidence_ids=("a", "b", "c"))
    model = ScriptedAdditionModel(candidate.name if with_candidate else None, candidate.content)
    active_model = model
    created = []

    class EntryPolicy(CodingFixturePolicy):
        def decide(self, request):
            if (request.action == ActionType.PROCESS_RUN
                    and tuple(request.arguments.get("command", ()))
                    == ("docker", "image", "inspect", verification_image)):
                return FixturePolicy.decide(self, request)
            return super().decide(request)

    def isolated_factory(settings):
        if settings.storage.metadata == source_settings.storage.metadata:
            # Fresh, unstarted source authority for commit assembly.
            return create_runtime(settings)
        runtime = create_runtime(settings)
        policy = EntryPolicy()
        policy.require_image_approval = approval_kind == "process"
        runtime._process_broker = ProcessBroker(policy, approvals=runtime.approvals)
        runtime._file_broker = FileBroker(policy)
        agent = runtime.agents.get(settings.runtime.default_agent)
        agent.model = active_model
        agent.executor = ActionExecutor(ToolExecutor(tools=runtime.tools, policy_engine=policy,
            approvals=runtime.approvals, policy=SecurityPolicy(
                always_allow=frozenset({"skill_view"} if approval_kind == "tool" else
                                      {"isolated_coding_command", "skill_view"}))))

        async def forbidden_start():
            raise AssertionError("Generic Runtime.start must not run")

        runtime.start = forbidden_start
        created.append(runtime)
        return runtime

    metadata = None
    try:
        workspace = await source.open_workspace(original)
        task = await source.create_task(workspace.id, "Implement integer addition in app.py",
            execution_mode="code", metadata={"required_paths": ["app.py"],
                "require_acceptance_tests": True,
                "acceptance_spec": {"cases": [{"id": "normal", "target": "app:add",
                    "args": [2, 3], "expected_output": 5}]}})
        await source.storage.metadata.save_artifact(candidate)
        from Sprout.evolution.isolated_replay import content_digest

        pinned = ArtifactSnapshot(candidate.id, candidate.version,
                                  content_digest(candidate.content))
        monkeypatch.setattr("Sprout.runtime.factory.create_runtime", isolated_factory)
        settings = source_settings
        result = await asyncio.wait_for(ReplayRunner(source).run(task.id,
            artifact_snapshot=pinned if with_candidate else None,
            revision=revision, settings=settings, output_dir=tmp_path / "experiments",
            cli_principal=current_cli_principal(), verification_commands=("pytest",),
            regression_commands=("pytest",), oracle_paths=("oracles",),
            verification_image=verification_image, coding_image=image, temporal_host=host,
            temporal_namespace=namespace,
            regression_spec={"cases": [{"id": "zero", "target": "app:add",
                "args": [-1, 1], "expected_output": 0}]}), timeout=90)
        assert result.status == "waiting_approval", result
        assert result.measurement_status == "not_measured"
        assert len(created) == 1 and created[0]._isolated_lifecycle_owned is True
        assert model.commands == (0 if approval_kind == "process" else 1)
        if with_candidate:
            database = Path(result.evidence_dir) / "metadata.db"
            first = open_metadata_store(str(database))
            try:
                original_nodes = await first.list_execution_nodes(result.task_id)
                assert not any(node.type is NodeType.EVALUATION for node in original_nodes)
            finally:
                first.close()
            options = dict(settings=settings, principal=current_cli_principal(),
                           source_metadata=source.storage.metadata)
            listed = await continue_coding(database, result.task_id, **options)
            assert listed["status"] == "waiting_approval"
            assert len(listed["pending_approvals"]) == 1
            approval_id = listed["pending_approvals"][0]["approval_id"]
            assert listed["pending_approvals"][0]["tool"] == (
                "process_run" if approval_kind == "process" else "isolated_coding_command")
            resumed = await asyncio.wait_for(continue_coding(database, result.task_id,
                approval_id=approval_id, **options), timeout=60)
            assert resumed["status"] == "waiting_approval", resumed
            assert resumed["git_executed"] is False
            assert model.commands == 1  # Persisted tool call, not a new model edit request.
            assert len(created) == 3
            reopened = open_metadata_store(str(database))
            try:
                for previous in original_nodes:
                    if previous.status == "completed":
                        assert await reopened.get_execution_node(previous.id) == previous
            finally:
                reopened.close()
        assert model.skill_read is with_candidate
        assert (await source.storage.metadata.get_artifact(candidate.id)).status is (
            ArtifactStatus.CANDIDATE)
        assert (await source.get_task(task.id)).status == "created"
        assert not await source.storage.metadata.list_execution_nodes(task.id)
        assert git(original, "rev-parse", "HEAD") == revision
        assert git(original, "status", "--porcelain") == ""
        metadata = open_metadata_store(str(Path(result.evidence_dir) / "metadata.db"))
        nodes = await metadata.list_execution_nodes(result.task_id)
        evaluation = next(node for node in nodes if node.type is NodeType.EVALUATION)
        assert evaluation.metadata["output"]["verification_report"]["verified"] is True
        submissions = await metadata.list_task_submissions(result.task_id)
        assert len(submissions) == (2 if with_candidate else 1)
        assert submissions[0]["host"] == host and submissions[0]["namespace"] == namespace
        assert submissions[0]["task_queue"].startswith("sprout-growth-")
        assert len({item["task_queue"] for item in submissions}) == 1
        if with_candidate:
            parked = await continue_coding(Path(result.evidence_dir) / "metadata.db",
                result.task_id, settings=settings, principal=current_cli_principal(),
                source_metadata=source.storage.metadata)
            assert "commit-*" in parked["next_action"]
            assert parked["proposal_id"]
        if approval_kind == "tool":
            from Sprout.evolution.comparison_refresh import refresh_comparison
            from Sprout.evolution.replay_pair import write_pair

            old_measurement = Path(result.measurement_path).read_bytes()
            await commit_and_measure(result, settings=settings, host=host, namespace=namespace)
            assert Path(result.measurement_path).read_bytes() == old_measurement
            active_model = ScriptedAdditionModel()
            baseline = await asyncio.wait_for(ReplayRunner(source).run(task.id,
                revision=revision, settings=settings, output_dir=tmp_path / "experiments",
                cli_principal=current_cli_principal(), verification_commands=("pytest",),
                regression_commands=("pytest",), oracle_paths=("oracles",),
                verification_image=verification_image, coding_image=image, temporal_host=host,
                temporal_namespace=namespace,
                regression_spec={"cases": [{"id": "zero", "target": "app:add",
                    "args": [-1, 1], "expected_output": 0}]}), timeout=90)
            assert baseline.status == "waiting_approval", baseline
            baseline_db = Path(baseline.evidence_dir) / "metadata.db"
            pending = await continue_coding(baseline_db, baseline.task_id, **options)
            await continue_coding(baseline_db, baseline.task_id,
                approval_id=pending["pending_approvals"][0]["approval_id"], **options)
            await commit_and_measure(baseline, settings=settings, host=host, namespace=namespace)
            pair = Path(write_pair(tmp_path, baseline, result))
            old_pair = pair.read_bytes()
            refreshed = refresh_comparison(pair)
            readiness = refreshed["report"]["validation_readiness"]
            assert readiness["observation_gates_passed"], refreshed
            assert pair.read_bytes() == old_pair and not refreshed["validation_committed"]
            assert git(original, "rev-parse", "HEAD") == revision
            assert git(original, "status", "--porcelain") == ""
            assert (await source.storage.metadata.get_artifact(candidate.id)).status is (
                ArtifactStatus.CANDIDATE)
            from Sprout.evolution.validation_handoff import validate_comparison

            validated = await validate_comparison(source.storage.metadata, candidate.id,
                                                   Path(refreshed["pair_manifest"]))
            assert validated["status"] == "validated", validated
            assert validated["published"] is False
        # Clean only exact completed, labelled, stopped containers owned by this test.
        for node in nodes:
            for slot in ("baseline-acceptance-0", "evaluation-0", "auxiliary-0",
                         "evaluation-acceptance-0", "evaluation-regression-0", "evaluation-poc-0"):
                record = await metadata.get_coding_execution(
                    str(uuid5(NAMESPACE_URL, node.id + ":coding:" + slot)))
                if record and record["state"] == "completed":
                    command = record["command"]
                    inspect_and_remove_owned_stopped(command[command.index("--name") + 1],
                                                     Path(result.evidence_dir))
    finally:
        if metadata is not None:
            metadata.close()
        await source.stop()
