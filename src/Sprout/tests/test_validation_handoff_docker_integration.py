"""Real paired Docker measurement -> candidate validation; controlled completed tasks.

No model-generated code or improvement claim. Only the candidate skill registry
differs in the positive case; the negative case seeds an explicit regression.
Existing fixture code is independently measured on both sides.
"""

import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from Sprout.context.context import AgentContext
from Sprout.evolution.candidate import GrowthCandidateBuilder
from Sprout.evolution.candidate_manager import CandidateManager
from Sprout.evolution.comparison_report import comparison_report
from Sprout.evolution.isolated_replay import content_digest, git, prepare
from Sprout.evolution.models import EvidenceRef, GrowthSignal
from Sprout.evolution.regression_spec import freeze
from Sprout.evolution.replay_config import configuration_contract
from Sprout.evolution.replay_docker import measure_docker
from Sprout.evolution.replay_functions import measure_functions
from Sprout.evolution.replay_measurement import write_evidence
from Sprout.evolution.replay_oracle import snapshot
from Sprout.evolution.replay_pair import write_pair
from Sprout.evolution.replay_plan import build_plan
from Sprout.evolution.replay_runner import ReplayResult
from Sprout.evolution.replay_snapshot import runtime_snapshot
from Sprout.execution.acceptance import render_acceptance_tests
from Sprout.gateway.local_identity import creator_record, current_cli_principal
from Sprout.runtime.factory import create_runtime
from Sprout.skills.models import Skill, TrustLevel
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import Task, TaskStatus
from Sprout.tests.test_coding_docker_integration import FixturePolicy
from Sprout.tests.test_published_skill_consumption import MARKER, consume_skill
from Sprout.tests.test_replay_resume_docker_integration import clean_owned_containers
from Sprout.trajectory.models import ArtifactSnapshot

IMAGE = os.getenv("SPROUT_VERIFICATION_TEST_IMAGE", "")
pytestmark = pytest.mark.skipif(not IMAGE, reason="Explicit pinned verification image required")


@pytest.mark.parametrize("regression_failure", [False, True],
                         ids=["validated", "regression-failed"])
async def test_real_pair_handoff_via_independent_cli(
    tmp_path, regression_failure, publication_sink=None,
):
    from Sprout.config.loader import default_settings

    original = tmp_path / "original"
    original.mkdir()
    (original / "tests").mkdir()
    acceptance = AcceptanceSpec.from_payload({"cases": [
        {"id": "normal", "target": "app:add", "args": [2, 3], "expected_output": 5}]})
    regression = {"cases": [
        {"id": "zero", "target": "app:add", "args": [-1, 1], "expected_output": 0}]}
    (original / "app.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (original / "tests/test_acceptance.py").write_text(
        render_acceptance_tests(acceptance), encoding="utf-8", newline="\n")
    git(original, "init", "-q")
    git(original, "add", ".")
    git(original, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", "paired measurement fixture")
    revision = git(original, "rev-parse", "HEAD")
    signal = GrowthSignal(type="skill_improvement", title="Integer addition procedure",
        description=("Inspect actual failure output before changing integer addition. "
                     "Implement app.add using a + b, then run the frozen tests and regressions.\n" +
                     MARKER), evidence=tuple(EvidenceRef("controlled-demo", f"fixture-{i}")
                                            for i in range(3)),
        confidence=0.9, impact_score=0.8, recurrence_score=0.8, severity_score=0.5)
    artifact = GrowthCandidateBuilder().build(signal).artifact
    authority = tmp_path / "authority"
    authority.mkdir()
    store = open_metadata_store(str(authority / "metadata.db"))
    results = []
    try:
        await store.save_artifact(artifact)
        for side in ("baseline", "candidate"):
            settings = default_settings()
            settings.model.provider = "echo"
            directory, repo, _, settings = prepare(original, revision, tmp_path / side, settings)
            if regression_failure and side == "candidate":
                # Controlled negative fixture, not model-generated code.
                (repo / "app.py").write_text(
                    "def add(a, b): return 999 if a == -1 else a + b\n", encoding="utf-8")
            runtime = create_runtime(settings)
            runtime._process_broker._policy = FixturePolicy()  # Explicit test-only Docker consent.
            task = None
            try:
                bound = (ArtifactSnapshot(artifact.id, artifact.version, content_digest(
                    artifact.content)) if side == "candidate" else None)
                if bound:
                    runtime.skills.register(Skill(name=artifact.name, version=artifact.version,
                        instructions=artifact.content, trust=TrustLevel.TRUSTED.value))
                workspace = await runtime.open_workspace(repo)
                frozen = snapshot(repo, ("tests",))
                regression_hash = freeze(directory, regression)
                plan = build_plan(source_task_id="controlled-pair-source", revision=revision,
                    mode="code", original=original, repo=repo, tests=("pytest",),
                    regressions=("pytest",), oracle_paths=("tests",), frozen=frozen, image=IMAGE,
                    candidate=asdict(bound) if bound else None,
                    configuration=configuration_contract(settings),
                    runtime=runtime_snapshot(runtime),
                    creator=creator_record(current_cli_principal()))
                task = Task(workspace_id=workspace.id, actor=current_cli_principal(), source="cli",
                    status=TaskStatus.COMPLETED, instruction="Verify integer addition",
                    metadata={"execution_mode": "code", "replay_plan": plan,
                        "acceptance_spec": acceptance.to_payload(),
                        "regression_fingerprint": regression_hash})
                await runtime.storage.metadata.save_task(task)
                kwargs = dict(directory=directory, repo=repo, original=original,
                              image=IMAGE, oracle_paths=("tests",), frozen=frozen)
                records = await measure_docker(runtime, task.id, ("pytest",), ("pytest",), **kwargs)
                functions = await measure_functions(runtime, task.id, **kwargs,
                    acceptance_spec=acceptance.to_payload(), regression_fingerprint=regression_hash)
                regressions = await measure_functions(runtime, task.id, **kwargs,
                    acceptance_spec=None, regression_fingerprint=regression_hash,
                    purpose="regression")
                report = {"schema_version": 1, "source_task_id": "controlled-pair-source",
                    "task_id": task.id, "revision": revision,
                    "candidate_id": artifact.id if bound else None,
                    "candidate_hash": bound.content_hash if bound else None,
                    "test_commands": ["pytest"], "regression_commands": ["pytest"],
                    "task_status": "completed", "records": records, "function_records": functions,
                    "regression_records": regressions, "regression_fingerprint": regression_hash,
                    "acceptance_spec": acceptance.to_payload(), "oracle_paths": ["tests"],
                    "oracle_snapshot": frozen,
                    "oracle_unchanged": snapshot(repo, ("tests",)) == frozen,
                    "measurement_status": "observed", "validation_committed": False}
                path = write_evidence(directory, report)
                results.append(ReplayResult(task_id=task.id, status=TaskStatus.COMPLETED,
                    artifact_snapshot=bound, source_task_id="controlled-pair-source",
                    workspace_revision=revision, evidence_dir=str(directory),
                    measurement_path=path))
            finally:
                try:
                    if task is not None:
                        names = []
                        for slot in (":verification:test-0", ":verification:regression-0",
                                     ":function-acceptance:normal", ":function-regression:zero"):
                            saved = await runtime.storage.metadata.get_verification_execution(
                                str(uuid5(NAMESPACE_URL, task.id + slot)))
                            if saved and saved["state"] == "completed":
                                names.append(saved["container_name"])
                        clean_owned_containers(names, directory)
                finally:
                    await runtime.storage.close()
        pair = write_pair(tmp_path, *results)
        observed = comparison_report(Path(pair))
        assert observed["validation_readiness"]["observation_gates_passed"] is (
            not regression_failure), observed
        config = authority / "source.toml"
        storage = {slot: "sqlite:///" + (authority / (slot + ".db")).as_posix()
                   for slot in ("metadata", "operational", "session", "context", "usage")}
        storage.update(knowledge="memory://", cache="memory", vectors="memory", graph="none",
                       trajectory_dir=str(authority / "trajectory"),
                       blobs_dir=str(authority / "blobs"),
                       project_root=str(authority / "projects"))
        config.write_text('skills_dir = ' + json.dumps(str(authority / "skills")) +
            '\n[model]\nprovider = "echo"\n[storage]\n' +
            "\n".join(key + " = " + json.dumps(value) for key, value in storage.items()) +
            '\n[storage.observations]\nenabled = false\n[memory]\nhome = ' +
            json.dumps(str(authority / "memory")) + "\n", encoding="utf-8")
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "Sprout", "evolution",
            "candidate-validate", artifact.id, "--pair", pair, "--config", str(config),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=30)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0, (out, err)
        verdict = json.loads(out)
        assert verdict["published"] is False
        candidate_report = await CandidateManager(store).get_candidate_report(artifact.id)
        if regression_failure:
            assert verdict["status"] == "waiting_validation"
            assert verdict["validation_committed"] is False
            assert "paired_regression_cases_not_all_passed" in verdict["blockers"]
            assert candidate_report["status"] == "candidate"
            assert not candidate_report["validation_current"]
        else:
            assert verdict["status"] == "validated" and verdict["validation_committed"] is True
            assert candidate_report["validation_current"]
            assert candidate_report["validation"]["source_run_ids"] == [r.task_id for r in results]
        assert not candidate_report["publication"]
        # Explicit human-equivalent CLI approval is a separate process/action,
        # never an automatic consequence of the measurement verdict.
        approval = await asyncio.create_subprocess_exec(sys.executable, "-m", "Sprout",
            "evolution", "approve-candidate", artifact.id, "--config", str(config),
            "--by", "explicit-integration-operator", stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            approval_out, approval_err = await asyncio.wait_for(approval.communicate(), timeout=30)
        finally:
            if approval.returncode is None:
                approval.kill()
                await approval.wait()
        skill_path = authority / "skills" / (artifact.name + ".toml")
        if regression_failure:
            assert approval.returncode != 0
            assert not skill_path.exists()
            assert (await CandidateManager(store).get_candidate_report(artifact.id))[
                "status"] == "candidate"
        else:
            assert approval.returncode == 0, (approval_out, approval_err)
            assert b"published:" in approval_out
            assert skill_path.is_file()
            published = await CandidateManager(store).get_candidate_report(artifact.id)
            assert published["status"] == "published"
            assert published["publication"]["state"] == "completed"
            assert published["publication"]["decided_by"] == "explicit-integration-operator"
            from Sprout.config.loader import load_settings

            restarted = create_runtime(load_settings(config))
            try:
                skill = restarted.skills.get(artifact.name)
                assert skill.instructions == artifact.content and skill.version == artifact.version
                assert skill.trust == TrustLevel.TRUSTED.value
                context = AgentContext(session=None, skills=restarted.skills.list())
                assert artifact.name in {s.name for s in context.visible_skills()}
                model, response = await consume_skill(restarted, artifact.name, artifact.content)
                assert model.saw_index and model.saw_body and model.calls == 2
                assert response.content == MARKER
                if publication_sink is not None:
                    publication_sink.update(artifact_id=artifact.id, name=artifact.name,
                        version=artifact.version, content_hash=content_digest(artifact.content),
                        skills_dir=str(authority / "skills"), pair_path=pair,
                        source_run_ids=[r.task_id for r in results], source_config=str(config),
                        publication_state=published["publication"]["state"],
                        signal_id=signal.id,
                        signal_source="controlled fixture, not production learning")
            finally:
                await restarted.storage.close()
        assert git(original, "rev-parse", "HEAD") == revision
    finally:
        store.close()
