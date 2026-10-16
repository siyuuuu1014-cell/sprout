import pytest

from Sprout.evolution.isolated_replay import git
from Sprout.sandbox.isolated_git_worktree import IsolatedGitWorktreeSandbox
from Sprout.workspace.models import Workspace


def fixture(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8", newline="")
    git(repo, "add", "app.py")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
        "-c", "commit.gpgsign=false", "commit", "-qm", "baseline")
    return repo, IsolatedGitWorktreeSandbox(Workspace(id="workspace", root=repo))


async def test_real_worktree_disables_checkout_hook_and_supports_data_diff(tmp_path):
    repo, provider = fixture(tmp_path)
    # If invoked, this harmless fixture hook would make worktree add fail.
    hook = repo / ".git/hooks/post-checkout"
    hook.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8", newline="")
    hook.chmod(0o755)
    ref = await provider.create()
    (ref.root / "app.py").write_text("value = 2\n", encoding="utf-8", newline="")
    assert "app.py" in await provider.changed_files(ref)
    diff = await provider.diff(ref)
    assert "+value = 2" in diff.diff_text


async def test_filter_configuration_blocks_before_worktree_creation(tmp_path):
    repo, provider = fixture(tmp_path)
    git(repo, "config", "filter.custom.clean", "fixture-not-executed")
    with pytest.raises(RuntimeError, match="filters require"):
        await provider.create()
    assert not list(tmp_path.glob(".sprout-*"))


async def test_worktree_git_pointer_cannot_redirect_to_another_repository(tmp_path, monkeypatch):
    repo, provider = fixture(tmp_path)
    ref = await provider.create()
    marker = ref.root / ".git"
    original_read = type(marker).read_text
    def redirected_read(path, *args, **kwargs):
        if path == marker:
            return "gitdir: " + str(repo / ".git") + "\n"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(type(marker), "read_text", redirected_read)
    with pytest.raises(ValueError, match="escapes"):
        await provider.changed_files(ref)


async def test_run_code_is_always_blocked(tmp_path):
    _, provider = fixture(tmp_path)
    with pytest.raises(RuntimeError, match="node-owned container"):
        await provider.run_code(None, "python", "raise AssertionError('must not run')")
