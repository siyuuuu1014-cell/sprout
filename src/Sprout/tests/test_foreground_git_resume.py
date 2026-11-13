import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.node_result_source import payload_digest
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.runtime.foreground_git_coding import ForegroundGitCoding
from Sprout.sandbox.isolated_git_worktree import IsolatedGitWorktreeSandbox
from Sprout.workspace.models import Workspace, WorkspaceKind


def settled(kind, output):
    return ExecutionNode(
        id=f"task:{kind.value}", task_id="task", type=kind, status=NodeStatus.COMPLETED,
        metadata={"execution_guard": {"state": "completed", "owner": "owner"}, "output": output},
    )


@pytest.mark.parametrize("corrupt", [None, "candidate", "verification", "write_scope", "diff"])
async def test_restore_review_uses_persisted_evaluation_and_manifest(tmp_path, corrupt):
    assembly = object.__new__(ForegroundGitCoding)
    assembly.task = SimpleNamespace(id="task")
    assembly.bundle = {"bundle_sha256": "a" * 64, "source_files": {"app.py": "b" * 64}}
    assembly.binding = {"repository": {"revision": "c" * 40}}
    assembly.paths = ("app.py",)
    assembly.plan = {"writes": [] if corrupt == "write_scope" else ["app.py"]}
    assembly.image = "sha256:" + "d" * 64
    spec = SimpleNamespace(to_payload=lambda: {"version": 2, "cases": []})
    assembly.acceptance = spec
    assembly.regression = spec
    evaluation = settled(NodeType.EVALUATION, {
        "verified": True, "status": "passed", "acceptance_spec": spec.to_payload(),
        "test_results": [{"passed": True}],
    })
    manifest = {"app.py": "e" * 64}
    review = settled(NodeType.APPROVAL, {
        "status": "reviewed", "files_changed": ["app.py"], "diff": "diff --git a/app.py b/app.py",
        "bundle_sha256": assembly.bundle["bundle_sha256"],
        "git_baseline": assembly.binding["repository"]["revision"],
        "evaluation_node_id": evaluation.id,
        "verification_sha256": ("f" * 64 if corrupt == "verification"
                                else payload_digest(evaluation.metadata["output"])),
        "input_manifest": {"app.py": "f" * 64} if corrupt == "candidate" else manifest,
        "test_results": evaluation.metadata["output"]["test_results"],
    })
    if corrupt == "diff":
        review.metadata["output"]["review_diff_sha256"] = "0" * 64
    sandbox = settled(NodeType.SANDBOX, {
        "id": "sandbox", "kind": "git_worktree", "root": str(tmp_path), "worktree_ref": "test",
    })
    intent = {"task_id": "task", "node_id": evaluation.id, "owner": "owner", "contract": {
        "acceptance": spec.to_payload(), "regression": spec.to_payload(), "image": assembly.image,
        "phase": "evaluation", "input_manifest": manifest,
    }}
    assembly.store = SimpleNamespace(
        list_execution_nodes=AsyncMock(return_value=[sandbox, evaluation, review]),
        get_coding_intent=AsyncMock(return_value=intent),
    )
    if corrupt:
        with pytest.raises(PermissionError, match="foreground_git_review"):
            await assembly.restore_review()
    else:
        ref, output = await assembly.restore_review()
        assert ref.root == tmp_path
        assert output == review.metadata["output"]
    assembly.store.list_execution_nodes.assert_awaited_once_with("task")

async def test_git_review_diff_includes_add_edit_and_delete(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git unavailable")
    root = tmp_path / "project"
    root.mkdir()
    for path in ("edited.py", "deleted.py"):
        (root / path).write_bytes(b"original = True\n")

    def run(*args):
        return subprocess.check_output([git, "--no-optional-locks", "-C", str(root), *args])

    run("init", "-q")
    run("add", "edited.py", "deleted.py")
    run("-c", "user.name=Sprout Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "Baseline")
    revision = run("rev-parse", "HEAD").decode().strip()
    index = (root / ".git" / "index").read_bytes()
    provider = IsolatedGitWorktreeSandbox(
        Workspace(id="workspace", root=root, kind=WorkspaceKind.GIT_REPOSITORY),
    )
    sandbox = await provider.create()
    try:
        (sandbox.root / "edited.py").write_bytes(b"modified = True\n")
        (sandbox.root / "deleted.py").unlink()
        (sandbox.root / "added.py").write_bytes(b"added = True\n")
        patch = await provider.review_patch(
            sandbox, revision, tracked=("edited.py", "deleted.py"), added=("added.py",),
        )
        assert "diff --git a/edited.py b/edited.py" in patch
        assert "diff --git a/deleted.py b/deleted.py" in patch
        assert "diff --git a/added.py b/added.py" in patch
        assert "deleted file mode" in patch and "new file mode" in patch
        assert run("status", "--short") == b""
        assert (root / ".git" / "index").read_bytes() == index
    finally:
        await provider.remove(sandbox)
