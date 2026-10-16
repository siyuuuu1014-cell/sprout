import base64
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from Sprout.evolution.coding_profile import coding_profile
from Sprout.evolution.isolated_replay import git
from Sprout.evolution.regression_spec import freeze
from Sprout.evolution.replay_plan import build_plan
from Sprout.execution.coding_session import CodingNodeSession
from Sprout.execution.isolated_git_evidence import inspect_commit_evidence
from Sprout.execution.isolated_git_plan import IsolatedGitPlan
from Sprout.execution.models import ProcessResult
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.sandbox.isolated_git_worktree import IsolatedGitWorktreeSandbox
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus
from Sprout.workspace.models import Workspace


async def fixture(tmp_path, *, actual="2", forged_report=False, auxiliary_mode=None):
    original, evidence = tmp_path / "original", tmp_path / "experiment"
    original.mkdir()
    repo = evidence / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "app.py").write_text("def run(): return 1\n", encoding="utf-8", newline="")
    git(repo, "add", "app.py")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", "baseline")
    workspace = Workspace(id="experiment", root=repo)
    sandbox = await IsolatedGitWorktreeSandbox(workspace).create()
    (sandbox.root / "app.py").write_text("def run(): return 2\n", encoding="utf-8", newline="")
    spec = {"cases": [{"id": "ok", "target": "app:run", "expected_output": 2}]}
    image = "sha256:" + "a" * 64
    plan = build_plan(source_task_id="source", revision=git(repo, "rev-parse", "HEAD"),
        mode="code", original=original, repo=repo, tests=("pytest",), regressions=("pytest",),
        oracle_paths=("tests",), frozen={"tests/example.py": "b" * 64}, image=image,
        configuration={"schema_version": 1}, runtime={"schema_version": 2},
        creator={"user_id": "fixture", "source": "cli", "authenticated": True},
        coding=coding_profile(image))
    task = Task(workspace_id=workspace.id, status=TaskStatus.RUNNING, metadata={
        "execution_mode": "code", "replay_plan": plan, "acceptance_spec": spec,
        "replay_source_task_id": "source", "regression_fingerprint": freeze(evidence, spec)})
    store = open_metadata_store(str(evidence / "metadata.db"))
    await store.save_workspace(workspace)
    await store.save_task(task)
    checks = ({"test_commands": ["python -m pytest"], "build_commands": ["python build.py"]}
              if auxiliary_mode is not None else {})
    node = ExecutionNode(task_id=task.id, type=NodeType.EVALUATION, metadata=checks)
    await store.save_execution_node(node)
    assert await store.claim_execution_node(node, "owner", "")
    node = await store.get_execution_node(node.id)

    class Runner:
        async def run_task_process(self, task_id, command, **kwargs):
            code = 0
            if command[:3] == ("docker", "image", "inspect"):
                stdout = json.dumps([{"Os": "linux", "Config": {"Labels": {
                    "sprout.role": "isolated-coding", "sprout.coding.schema": "1"}}}])
            else:
                stdout = actual
                inner = json.loads(base64.b64decode(command[-1]))
                if inner in (["python", "-m", "pytest"], ["python", "build.py"]):
                    stdout = "1 passed"
                    if auxiliary_mode == "skipped":
                        stdout = "1 passed, 1 skipped"
                    elif auxiliary_mode == "missing_tests":
                        stdout, code = "no tests ran", 5
                    elif auxiliary_mode == "failed":
                        stdout, code = "1 failed", 1
                    mount = next(arg for arg in command if arg.startswith("type=bind,src=")
                                 and arg.endswith(",dst=/workspace"))
                    exported = Path(mount.removeprefix("type=bind,src=").split(",dst=")[0])
                    (exported / "app.py").write_text("auxiliary build artifact")
            return ProcessResult(tuple(command), exit_code=code, stdout=stdout, started=True)

        async def authorize_task_process(self, task_id, command):
            return ProcessResult(tuple(command), allowed=True, started=False)

    coding_evidence = evidence / "coding-evidence"
    coding_evidence.mkdir()
    session = CodingNodeSession(store, Runner(), None, evidence=coding_evidence, original=original,
                                image=image, oracle_paths=("tests",), sandbox=sandbox,
                                task=task, node=node)
    auxiliary = await session.verify_auxiliary(node.metadata)
    output = await session.verify(acceptance_spec=spec, regression_spec=spec)
    assert auxiliary["waiting"] is False
    if forged_report:
        output = {"verified": True, "status": "passed"}
    finished = replace(node, status=NodeStatus.COMPLETED,
                       metadata={**node.metadata, "output": output})
    assert await store.finish_execution_node(finished, "owner", "completed")
    planner = IsolatedGitPlan(workspace, original=original, evidence=evidence)
    git_plan = await planner.commit_plan(sandbox.root)
    return store, task, node, sandbox, planner, git_plan


async def inspect(store, task, node, planner, plan):
    return await inspect_commit_evidence(store, task_id=task.id, evaluation_node_id=node.id,
                                         planner=planner, git_plan=plan)


async def test_persisted_raw_actuals_bind_source_but_never_authorize_write(tmp_path):
    store, task, node, _, planner, plan = await fixture(tmp_path)
    try:
        result = await inspect(store, task, node, planner, plan)
        assert result["evidence_binding_passed"] is True
        assert len(result["evidence_fingerprint"]) == 64
        assert result["write_authorized"] is False and result["validation_authorized"] is False
    finally:
        store.close()


@pytest.mark.parametrize("change", ["source", "export", "oracle"])
async def test_changed_source_export_or_frozen_oracle_blocks(tmp_path, change):
    store, task, node, sandbox, planner, plan = await fixture(tmp_path)
    try:
        if change == "source":
            (sandbox.root / "app.py").write_text("def run(): return 3\n")
            plan = await planner.commit_plan(sandbox.root)
        elif change == "export":
            exported = (planner.evidence / "coding-evidence" / ("coding-node-" + node.id)
                        / "coding-input/app.py")
            exported.write_text("changed")
        else:
            (planner.evidence / "regression-spec.json").write_text('{"cases": []}')
        result = await inspect(store, task, node, planner, plan)
        assert result["evidence_binding_passed"] is False
        assert result["write_authorized"] is False
    finally:
        store.close()


async def test_forged_passing_report_cannot_replace_failed_raw_output(tmp_path):
    store, task, node, _, planner, plan = await fixture(
        tmp_path, actual='{"passed": true}', forged_report=True)
    try:
        result = await inspect(store, task, node, planner, plan)
        assert result["evidence_binding_passed"] is False
        assert any("case_actual_does_not_match" in blocker for blocker in result["blockers"])
    finally:
        store.close()


@pytest.mark.parametrize("change", ["missing", "uncertain", "legacy_started"])
async def test_incomplete_or_legacy_process_evidence_blocks(tmp_path, monkeypatch, change):
    store, task, node, _, planner, plan = await fixture(tmp_path)
    original_get = store.get_coding_execution

    async def altered_get(identity):
        record = deepcopy(await original_get(identity))
        if change == "missing":
            return None
        if change == "uncertain":
            record["state"] = "uncertain"
        else:
            record["result"]["started"] = None
        return record

    monkeypatch.setattr(store, "get_coding_execution", altered_get)
    try:
        result = await inspect(store, task, node, planner, plan)
        assert result["evidence_binding_passed"] is False and not result["write_authorized"]
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["passed", "skipped", "missing_tests", "failed"])
async def test_auxiliary_raw_results_and_changed_build_copy_are_inspected(tmp_path, mode):
    store, task, node, _, planner, plan = await fixture(
        tmp_path, auxiliary_mode=mode, forged_report=True)
    try:
        result = await inspect(store, task, node, planner, plan)
        assert result["evidence_binding_passed"] is (mode == "passed")
        assert result["write_authorized"] is False
        if mode != "passed":
            assert any("auxiliary_check_not_passed" in item for item in result["blockers"])
    finally:
        store.close()


@pytest.mark.parametrize("change", ["missing", "uncertain", "legacy_started"])
async def test_auxiliary_execution_evidence_cannot_be_omitted(tmp_path, monkeypatch, change):
    store, task, node, _, planner, plan = await fixture(tmp_path, auxiliary_mode="passed")
    original_get = store.get_coding_execution

    async def altered_get(identity):
        record = deepcopy(await original_get(identity))
        if record and record["slot"].startswith("auxiliary-"):
            if change == "missing":
                return None
            if change == "uncertain":
                record["state"] = "uncertain"
            else:
                record["result"]["started"] = None
        return record

    monkeypatch.setattr(store, "get_coding_execution", altered_get)
    try:
        assert not (await inspect(store, task, node, planner, plan))["evidence_binding_passed"]
    finally:
        store.close()


async def test_empty_check_list_requires_frozen_empty_plan(tmp_path, monkeypatch):
    store, task, node, _, planner, plan = await fixture(tmp_path)
    original_get = store.get_coding_intent

    async def missing_empty_plan(identity):
        record = await original_get(identity)
        if record and "commands" in record.get("contract", {}):
            return None
        return record

    monkeypatch.setattr(store, "get_coding_intent", missing_empty_plan)
    try:
        result = await inspect(store, task, node, planner, plan)
        assert not result["evidence_binding_passed"]
        assert "frozen_auxiliary_plan_mismatch" in result["blockers"]
    finally:
        store.close()


@pytest.mark.parametrize("change", ["commands", "seal"])
async def test_auxiliary_plan_or_prelaunch_seal_drift_blocks(tmp_path, monkeypatch, change):
    store, task, node, _, planner, plan = await fixture(tmp_path, auxiliary_mode="passed")
    if change == "commands":
        original_get = store.get_execution_node

        async def changed_requirements(identity):
            record = await original_get(identity)
            return replace(record, metadata={**record.metadata, "test_commands": []})

        monkeypatch.setattr(store, "get_execution_node", changed_requirements)
    else:
        seal = (planner.evidence / "coding-evidence" / ("coding-node-" + node.id)
                / "auxiliary-0/baseline.json")
        seal.write_text('{"app.py": "changed"}')
    try:
        assert not (await inspect(store, task, node, planner, plan))["evidence_binding_passed"]
    finally:
        store.close()
