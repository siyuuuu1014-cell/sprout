import pytest

from Sprout.evolution.isolated_replay import git
from Sprout.execution.isolated_git_plan import IsolatedGitPlan
from Sprout.sandbox.isolated_git_worktree import IsolatedGitWorktreeSandbox
from Sprout.workspace.models import Workspace


def setup(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    evidence = tmp_path / "experiment"
    evidence.mkdir()
    repo = evidence / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8", newline="")
    git(repo, "add", "app.py")
    commit(repo, "baseline")
    workspace = Workspace(id="experiment", root=repo)
    return repo, IsolatedGitWorktreeSandbox(workspace), IsolatedGitPlan(
        workspace, original=original, evidence=evidence)


def commit(repo, message):
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", message)


async def test_commit_plan_is_readonly_and_changes_with_source(tmp_path):
    repo, provider, planner = setup(tmp_path)
    ref = await provider.create()
    base = git(repo, "rev-parse", "HEAD")
    (ref.root / "app.py").write_text("value = 2\n")
    first = await planner.commit_plan(ref.root)
    assert first["base_commit"] == base and first["write_authorized"] is False
    assert git(repo, "rev-parse", "HEAD") == base
    assert (repo / "app.py").read_text() == "value = 1\n"
    (ref.root / "app.py").write_text("value = 3\n")
    second = await planner.commit_plan(ref.root)
    assert first["diff_sha256"] != second["diff_sha256"]
    assert first["source_manifest"] != second["source_manifest"]
    with pytest.raises(ValueError, match="Unsupported"):
        await planner._run("git", "-C", str(repo), "reset", "--hard")


async def test_dirty_target_and_unreviewed_new_file_block(tmp_path):
    repo, provider, planner = setup(tmp_path)
    ref = await provider.create()
    (ref.root / "new.py").write_text("new = 1")
    with pytest.raises(ValueError, match="Untracked"):
        await planner.commit_plan(ref.root)
    (repo / "other.py").write_text("unrelated = 1")
    with pytest.raises(ValueError, match="unrelated"):
        await planner.commit_plan(ref.root)


async def test_rollback_requires_exact_head_and_parent_and_does_not_revert(tmp_path):
    repo, _, planner = setup(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("value = 2\n")
    git(repo, "add", "app.py")
    commit(repo, "applied")
    applied = git(repo, "rev-parse", "HEAD")
    plan = await planner.rollback_plan(applied_commit=applied, base_commit=base)
    assert plan["strategy"] == "new_revert_commit"
    assert plan["invalidates_current_validation"] and not plan["write_authorized"]
    assert git(repo, "rev-parse", "HEAD") == applied
    with pytest.raises(ValueError, match="single-parent"):
        await planner.rollback_plan(applied_commit=applied, base_commit=applied)
    with pytest.raises(ValueError, match="moved"):
        await planner.rollback_plan(applied_commit=base, base_commit=base)


def test_original_cannot_be_experiment_target(tmp_path):
    repo, _, planner = setup(tmp_path)
    with pytest.raises(ValueError, match="Independent"):
        IsolatedGitPlan(Workspace(id="invalid", root=repo), original=repo,
                        evidence=planner.evidence)
