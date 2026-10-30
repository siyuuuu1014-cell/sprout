"""Real Windows Receipt/G1/Apply regression; scripted agent, real effects."""

import hashlib
import json
import os
import tempfile
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from Sprout.gateway.rpc_gateway import RPCGateway
from Sprout.llm.messages import ToolCall
from Sprout.tests.r04_g1_native_cases import git
from Sprout.tests.test_r04_e_apply_native import prepare_apply_fixture

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Explicit Windows native entry required",
)


@pytest.mark.parametrize("mode", ["single", "multi", "no_changes", "conflict", "index_only"])
async def test_review_fix_native(tmp_path, monkeypatch, mode):
    ctx = await prepare_apply_fixture(tmp_path, monkeypatch, "cli")
    runtime, task, root, store = (
        ctx["runtime"], ctx["task"], ctx["root"], ctx["storage"].metadata
    )
    extras = ["src/second.py", "src/unchanged.py"]
    for p in extras:
        (root / p).write_bytes(b"def helper():\n    return 1\n")
    # Fixture baseline only; never touches the Sprout repository/index.
    git(root, "add", "--", *extras)
    git(root, "-c", "user.name=Review fixture", "-c", "user.email=review@example.invalid",
        "-c", "commit.gpgSign=false", "commit", "-m", "additional acceptance inputs")
    task = replace(task, metadata={**task.metadata, "required_paths": [*ctx["files"], *extras]})
    await store.save_task(task)
    changes = {} if mode in {"no_changes", "index_only"} else {"src/app.py": ctx["new"]}
    if mode == "multi":
        changes["src/second.py"] = "def helper():\n    return 2\n"
    response = ctx["model"].responses[1]
    ctx["model"].responses[1] = replace(
        response, content="No edit needed" if not changes else response.content,
        tool_calls=tuple(ToolCall(str(uuid4()), "sandbox_write_file", {"path": p, "content": t})
                         for p, t in changes.items()),
    )
    if mode == "index_only":
        factory = runtime._executor

        def executor(metadata):
            instance = factory(metadata)
            agent = instance._agent

            async def staged_then_restored(*args):
                result = await agent(*args)
                sandbox = next(iter(runtime._local_git_reviews.values()))["source"].sandbox.root
                p = sandbox / "src/app.py"
                original = p.read_bytes()
                p.write_bytes(b"def echo(value):\n    return 123\n")
                git(sandbox, "add", "--", "src/app.py")
                p.write_bytes(original)
                return result

            instance._agent = staged_then_restored
            return instance

        monkeypatch.setattr(runtime, "_executor", executor)
    exports = tempfile.mkdtemp(prefix="srg1-review-fix-")
    await runtime.configure_local_git_review(
        task.id, task.actor, executable=ctx["git"],
        executable_sha256="cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96",
        export_parent=exports,
    )
    before = {p: (root / p).read_bytes() for p in task.metadata["required_paths"]}
    evidence = {"mode": mode, "task": task.id, "exports": exports}
    try:
        result = await runtime._execute_task(task, artifact_snapshot=None)
        nodes = await store.list_execution_nodes(task.id)
        proposals = await store.list_change_proposals(task.id)
        evidence.update(result=str(result), nodes=[
            {"type": n.type.value, "status": n.status.value, "metadata": n.metadata}
            for n in nodes])
        assert runtime._local_acceptance_receipts
        payload = next(iter(runtime._local_acceptance_receipts.values()))[0]
        evidence["receipt"] = payload
        assert {"src/app.py", *extras}.issubset({x["path"] for x in payload["after"]})
        approval = next(n for n in nodes if n.type.value == "approval")
        if mode == "index_only":
            assert not proposals
            assert "local_git_plan_change_set_mismatch" in approval.metadata.get("error", "")
            return
        if mode == "no_changes":
            assert not proposals
            assert approval.metadata["output"]["status"] == "no_changes"
            assert not runtime._local_apply_plans
            assert all((root / p).read_bytes() == b for p, b in before.items())
            return
        assert len(proposals) == 1, evidence
        proposal = proposals[0]
        plan = proposal.metadata["apply_plan"]
        assert {f["path"] for f in plan["files"]} == set(changes)
        assert set(proposal.files_changed) == set(changes)
        assert set(proposal.metadata["git_diff"]["changed_paths"]) == set(changes)
        rpc = RPCGateway(runtime)
        shown = await rpc.handle({"id": 1, "method": "proposal.show",
                                  "params": {"proposal_id": proposal.id}}, principal=task.actor)
        evidence["shown"] = shown
        display = shown["result"]
        assert display["content_diff"] == proposal.metadata["content_diff"]
        assert display["content_diff_sha256"] == hashlib.sha256(
            display["content_diff"]["text"].encode()).hexdigest()
        assert display["git_diff"]["patches"] == proposal.metadata["git_diff"]["patches"]
        assert display["git_diff"]["sha256"] == proposal.metadata["git_diff"]["sha256"]
        assert display["apply_plan_sha256"] == proposal.metadata["apply_plan_sha256"]
        assert display["diff_format"] == {
            "git_diff": "git_no_index_managed_view", "content_diff": "unified_content_diff"}
        assert exports not in json.dumps(display) and str(tmp_path) not in json.dumps(display)
        assert "cwd" not in display["git_diff"]["commands"][0]
        denied = await rpc.handle({"id": 2, "method": "proposal.show",
                                   "params": {"proposal_id": proposal.id}},
                                  principal=replace(task.actor, user_id="another-user"))
        assert "error" in denied and "result" not in denied
        # A separate historical-shaped test Task must not enter this live Task's
        # single-Proposal publication/resume selection.
        legacy_task = replace(task, id=str(uuid4()))
        await store.save_task(legacy_task)
        legacy = replace(proposal, id=str(uuid4()), task_id=legacy_task.id,
                         metadata={}, diffs=proposal.diffs)
        await store.save_change_proposal(legacy)
        old = await rpc.handle({"id": 3, "method": "proposal.show",
                               "params": {"proposal_id": legacy.id}}, principal=task.actor)
        assert old["result"]["diffs"] == [d.diff_text for d in legacy.diffs]
        assert old["result"].get("content_diff") is None
        if mode == "conflict":
            (root / "src/app.py").write_bytes(b"# concurrent workspace edit\n")
        answer = await rpc.handle({"id": 4, "method": "proposal.approve",
                                   "params": {"proposal_id": proposal.id}}, principal=task.actor)
        evidence["approval_response"] = answer
        tx = await store.get_apply_transaction(
            str(uuid5(NAMESPACE_URL, "ordinary-apply:" + proposal.id)))
        evidence["transaction"] = tx
        if mode == "conflict":
            assert (root / "src/app.py").read_bytes() == b"# concurrent workspace edit\n"
            assert tx is None
        else:
            assert tx["phase"] == "COMPLETED"
            assert all((root / f["path"]).read_bytes() == f["new_text"].encode()
                       for f in plan["files"])
            approvals = await runtime._approvals.store.list_approvals()
            applies = [a for a in approvals if a.task_id == task.id and a.tool == "file.apply"]
            assert len(applies) == 1
        assert all((root / p).read_bytes() == b for p, b in before.items() if p not in changes)
    finally:
        evidence["after_sha256"] = {p: hashlib.sha256((root / p).read_bytes()).hexdigest()
                                    for p in before}
        evidence["before_sha256"] = {p: hashlib.sha256(b).hexdigest() for p, b in before.items()}
        (tmp_path / "REVIEW_FIX.json").write_text(json.dumps(evidence, default=str, indent=2))
        await runtime.stop()
        store.close()
