from copy import deepcopy
from types import SimpleNamespace

import pytest

from Sprout.config.loader import default_settings
from Sprout.evolution.comparison_conditions import condition_blockers
from Sprout.evolution.replay_config import configuration_contract
from Sprout.evolution.replay_plan import build_plan


def setup(tmp_path, monkeypatch):
    candidate = {"artifact_id": "candidate", "artifact_version": "1", "content_hash": "b" * 64}
    config = configuration_contract(default_settings())
    results, reports, facts = [], [], {}
    for name in ("baseline", "candidate"):
        directory = tmp_path / name
        repo = directory / "repo"
        repo.mkdir(parents=True)
        runtime = {"schema_version": 2, "implementation_fingerprint": "c" * 64,
                   "tools_fingerprint": "d" * 64, "skills_fingerprint": "e" * 64,
                   "external_tool_implementation": False, "skill_manifest": {}}
        if name == "candidate":
            runtime["skill_manifest"]["candidate-skill"] = {
                "fingerprint": "f" * 64, "version": "1", "instructions_hash": "b" * 64}
        plan = build_plan(source_task_id="source", revision="a" * 40, mode="code",
                          original=tmp_path / "original", repo=repo, tests=["pytest"],
                          regressions=["pytest"], oracle_paths=["tests"],
                          frozen={"tests/a": "hash"},
                          image="sha256:" + "a" * 64,
                          candidate=candidate if name == "candidate" else None,
                          configuration=config, runtime=runtime,
                          creator={"user_id": "owner", "source": "cli", "authenticated": True})
        facts[name] = {"status": "completed", "workspace_root": str(repo),
                       "task_input_fingerprint": "a" * 64,
                       "source": "cli", "actor": {"user_id": "owner", "source": "cli",
                                                    "authenticated": True},
                       "metadata": {"execution_mode": "code", "replay_plan": plan}}
        results.append(SimpleNamespace(evidence_dir=str(directory), task_id=name,
                                       artifact_snapshot=SimpleNamespace(**candidate)
                                       if name == "candidate" else None))
        reports.append({"source_task_id": "source", "revision": "a" * 40,
                        "test_commands": ["pytest"], "regression_commands": ["pytest"],
                        "oracle_snapshot": {"tests/a": "hash"}, "candidate_id": "candidate",
                        "candidate_hash": "b" * 64,
                        "records": [{"verification_image": "sha256:" + "a" * 64}]})
    monkeypatch.setattr("Sprout.evolution.comparison_conditions.recovery_facts",
                        lambda path, task_id: facts[task_id])
    return results, reports, facts


def test_same_recorded_environment_with_only_candidate_difference(tmp_path, monkeypatch):
    results, reports, _ = setup(tmp_path, monkeypatch)
    assert condition_blockers(results, reports) == []


@pytest.mark.parametrize("field", ["configuration", "implementation_fingerprint", "skill_manifest"])
def test_changed_environment_rejected_even_with_recomputed_plan_hash(tmp_path, monkeypatch, field):
    from Sprout.evolution.replay_oracle import digest

    results, reports, facts = setup(tmp_path, monkeypatch)
    plan = deepcopy(facts["candidate"]["metadata"]["replay_plan"])
    if field == "configuration":
        plan["payload"][field]["model_fingerprint"] = "1" * 64
    elif field == "skill_manifest":
        plan["payload"]["runtime_snapshot"][field]["extra"] = {"fingerprint": "2" * 64}
    else:
        plan["payload"]["runtime_snapshot"][field] = "3" * 64
    plan["fingerprint"] = digest(plan["payload"])
    facts["candidate"]["metadata"]["replay_plan"] = plan
    assert condition_blockers(results, reports)


def test_missing_plan_is_not_backfilled(tmp_path, monkeypatch):
    results, reports, facts = setup(tmp_path, monkeypatch)
    facts["candidate"]["metadata"].pop("replay_plan")
    assert condition_blockers(results, reports) == [
        "experiment_plan_evidence_unavailable_or_malformed"]


def test_real_sqlite_plan_binding_is_read_only(tmp_path, monkeypatch):
    import asyncio

    from Sprout.gateway.identity import Principal
    from Sprout.storage.local.sqlite.metadata import open_metadata_store
    from Sprout.storage.local.sqlite.verification_report import recovery_facts
    from Sprout.task.models import Task, TaskStatus
    from Sprout.workspace.models import Workspace

    results, reports, facts = setup(tmp_path, monkeypatch)
    monkeypatch.setattr("Sprout.evolution.comparison_conditions.recovery_facts", recovery_facts)

    async def save():
        for result in results:
            store = open_metadata_store(str(tmp_path / result.task_id / "metadata.db"))
            try:
                await store.save_workspace(Workspace(id=result.task_id,
                                                     root=tmp_path / result.task_id / "repo"))
                await store.save_task(Task(id=result.task_id, workspace_id=result.task_id,
                                          actor=Principal(user_id="owner", authenticated=True,
                                                          source="cli"), source="cli",
                                          status=TaskStatus.COMPLETED,
                                          metadata=facts[result.task_id]["metadata"]))
            finally:
                store.close()

    asyncio.run(save())
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.db")}
    assert condition_blockers(results, reports) == []
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*.db")}


def test_different_task_instruction_or_budget_is_not_same_experiment(tmp_path, monkeypatch):
    results, reports, facts = setup(tmp_path, monkeypatch)
    facts["candidate"]["task_input_fingerprint"] = "different"
    assert "experiment_task_inputs_mismatch" in condition_blockers(results, reports)
