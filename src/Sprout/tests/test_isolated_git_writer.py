import pytest

from Sprout.evolution.isolated_replay import git
from Sprout.execution.git_transaction_contract import commit_write_contract
from Sprout.execution.isolated_git_writer import _CommitCommands, _write_commit
from Sprout.tests.test_isolated_git_plan import setup


async def prepared(tmp_path):
    repo, provider, planner = setup(tmp_path)
    ref = await provider.create()
    (ref.root / "app.py").write_text("value = 2\n", encoding="utf-8", newline="")
    plan = await planner.commit_plan(ref.root)
    contract = commit_write_contract(plan, author_name="Explicit Operator",
        author_email="operator@invalid.example", message="Reviewed experiment change")
    return repo, ref, planner, plan, contract


async def test_real_commit_has_exact_content_parent_identity_and_unchanged_config(tmp_path):
    repo, ref, planner, plan, contract = await prepared(tmp_path)
    before = (repo / ".git/config").read_bytes()
    original_before = list(planner.original.iterdir())
    result = await _write_commit(planner, plan, contract)
    assert result["base_commit"] == plan["base_commit"]
    assert result["result_commit"] == git(repo, "rev-parse", "HEAD")
    assert (repo / "app.py").read_text() == "value = 2\n"
    assert (ref.root / "app.py").read_text() == "value = 2\n"
    assert (repo / ".git/config").read_bytes() == before
    assert list(planner.original.iterdir()) == original_before
    assert not git(repo, "status", "--porcelain")


async def test_source_drift_rejected_before_target_mutation(tmp_path):
    repo, ref, planner, plan, contract = await prepared(tmp_path)
    (ref.root / "app.py").write_text("value = 3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        await _write_commit(planner, plan, contract)
    assert git(repo, "rev-parse", "HEAD") == plan["base_commit"]
    assert (repo / "app.py").read_text() == "value = 1\n"


async def test_commit_failure_preserves_staged_changes_without_reset(tmp_path, monkeypatch):
    repo, _, planner, plan, contract = await prepared(tmp_path)
    run = _CommitCommands._run

    async def fail(self, *args):
        if "commit" in args:
            raise RuntimeError("Injected commit failure")
        return await run(self, *args)

    monkeypatch.setattr(_CommitCommands, "_run", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        await _write_commit(planner, plan, contract)
    assert git(repo, "rev-parse", "HEAD") == plan["base_commit"]
    assert (repo / "app.py").read_text() == "value = 2\n"
    assert git(repo, "diff", "--cached")
