"""Internal Git mutation adapter; never a CLI, tool or authorization entry point."""

import hashlib
from pathlib import Path
from uuid import uuid4

from Sprout.execution.git_transaction_contract import validate_commit_write_contract
from Sprout.execution.isolated_git_plan import IsolatedGitPlan
from Sprout.execution.verification_export import export_input


class _CommitCommands(IsolatedGitPlan):
    _operations = IsolatedGitPlan._operations | {"apply", "commit", "show"}


async def _write_commit(planner, plan, contract):
    """Coordinator-only: caller must already own claim and consumed exact grant.

    No recovery/reset/cleanup on mutation failure. A caller must retain its
    durable claim as uncertain, including on cancellation or observation failure.
    """
    validate_commit_write_contract(contract, plan)
    if plan != await planner.commit_plan(plan["source"]):
        raise ValueError("Commit plan changed before write")
    commands = _CommitCommands(planner._workspace, original=planner.original,
                               evidence=planner.evidence)
    target = planner.repository_root
    diff = await planner._run("git", "-C", plan["source"], "diff", "--no-ext-diff",
                              "--binary", "HEAD", "--")
    if not diff or hashlib.sha256(diff.encode()).hexdigest() != plan["diff_sha256"]:
        raise ValueError("Exact nonempty reviewed diff required")
    # Runtime evidence, outside both original and experiment repository.
    patch = planner.evidence / ("commit-patch-" + str(uuid4()) + ".diff")
    patch.write_text(diff, encoding="utf-8", newline="")
    await commands._run("git", "-C", str(target), "apply", "--check", "--index", str(patch))
    # Recheck immediately before the first repository mutation.
    if plan != await planner.commit_plan(plan["source"]):
        raise ValueError("Commit plan changed during write preflight")
    await commands._run("git", "-C", str(target), "apply", "--index", str(patch))
    await commands._run("git", "-c", "user.name=" + contract["author_name"],
        "-c", "user.email=" + contract["author_email"], "-c", "commit.gpgSign=false",
        "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "i18n.commitEncoding=UTF-8",
        "-C", str(target), "commit", "--no-gpg-sign", "--no-verify", "-m", contract["message"])
    result = await planner._head(target)
    parents = (await planner._run("git", "-C", str(target), "rev-list", "--parents",
                                  "-n", "1", result)).split()
    if parents != [result, plan["base_commit"]]:
        raise RuntimeError("Observed commit parent mismatch; retain for reconciliation")
    identity = await commands._run("git", "-C", str(target), "show", "--no-patch",
        "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", result, "--")
    expected = [contract["author_name"], contract["author_email"],
                contract["author_name"], contract["author_email"], contract["message"]]
    if identity.rstrip("\n").split("\x00") != expected:
        raise RuntimeError("Commit identity/message mismatch; retain for reconciliation")
    committed_diff = await planner._run("git", "-C", str(target), "diff", "--no-ext-diff",
        "--binary", plan["base_commit"], result, "--")
    if hashlib.sha256(committed_diff.encode()).hexdigest() != plan["diff_sha256"]:
        raise RuntimeError("Committed tree diff mismatch; retain for reconciliation")
    await planner._clean_target()
    manifest = export_input(Path(target), planner.evidence / "committed-preview",
                            planner.original, manifest_only=True)
    if manifest != plan["source_manifest"]:
        raise RuntimeError("Observed commit content mismatch; retain for reconciliation")
    return {"base_commit": plan["base_commit"], "result_commit": result}
