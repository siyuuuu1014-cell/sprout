import pytest

from Sprout.evolution.replay_plan import build_plan
from Sprout.evolution.replay_recovery import inspect_recovery


@pytest.mark.parametrize("change,blocker", [
    ({"node_count": 1}, "existing_execution_nodes"),
    ({"claim_count": 1}, "existing_verification_claims"),
    ({"status": "completed"}, "task_not_waiting_resource"),
    ({"resume_count": 1}, "existing_resume_ownership"),
])
def test_previous_execution_blocks_recovery(tmp_path, monkeypatch, change, blocker):
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = build_plan(
        source_task_id="source", revision="a" * 40, mode="code",
        original=tmp_path / "original", repo=repo,
        tests=("pytest",), regressions=("pytest tests",), oracle_paths=("tests",),
        frozen={"tests/test_a.py": "b" * 64}, image="sha256:" + "c" * 64,
    )
    facts = {"status": "waiting_resource", "workspace_root": str(repo),
             "metadata": {"replay_plan": plan, "execution_mode": "code",
                          "replay_source_task_id": "source"},
             "node_count": 0, "claim_count": 0, **change}
    monkeypatch.setattr("Sprout.evolution.replay_recovery.recovery_facts", lambda *a: facts)
    monkeypatch.setattr("Sprout.evolution.replay_recovery.git",
                        lambda root, *args: "a" * 40 if args[0] == "rev-parse" else "")
    monkeypatch.setattr("Sprout.evolution.replay_recovery.integrity", lambda *a: True)
    report = inspect_recovery(tmp_path / "metadata.db", "task")
    assert blocker in report["blockers"]
    assert not report["preflight_checks_passed"]
    assert not report["resume_authorized"]
