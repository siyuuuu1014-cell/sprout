"""Native Local Receipt/G1 negative integration; real receipt, Git and Guard."""

import json
import os
import tempfile
from copy import copy
from dataclasses import replace
from pathlib import Path

import pytest

from Sprout.execution.git_s1 import _ReviewView
from Sprout.llm.messages import ToolCall
from Sprout.tests.test_r04_e_apply_native import prepare_apply_fixture

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Explicit Windows native entry required",
)


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "baseline",
        "forged_receipt",
        "untracked",
        "baseline_after_review",
        "dirty_target",
        "deleted_input",
        "renamed_input",
    ],
)
async def test_local_receipt_g1_native(tmp_path, monkeypatch, mode):
    ctx = await prepare_apply_fixture(tmp_path, monkeypatch, "cli")
    runtime, task, store = ctx["runtime"], ctx["task"], ctx["storage"].metadata
    if mode == "untracked":
        extra = "src/new.py"
        (ctx["root"] / extra).write_bytes(b"def helper(): return 1\n")
        task = replace(
            task,
            metadata={**task.metadata, "required_paths": [*task.metadata["required_paths"], extra]},
        )
        await store.save_task(task)
        register = runtime._register_frozen_sandbox

        async def untracked_input(sandbox):
            await register(sandbox)
            written = await runtime._file_broker.write_text(
                sandbox,
                extra,
                "def helper(): return 1\n",
                task_id=task.id,
                scope=task.delegation_scope,
            )
            assert written.wrote, written

        monkeypatch.setattr(runtime, "_register_frozen_sandbox", untracked_input)
        model = ctx["model"]
        model.responses[1] = replace(
            model.responses[1],
            tool_calls=(
                *model.responses[1].tool_calls,
                ToolCall(
                    "create-new",
                    "sandbox_write_file",
                    {"path": extra, "content": "def helper(): return 2\n"},
                ),
            ),
        )
    if mode == "dirty_target":
        (ctx["root"] / "src/app.py").write_bytes(
            (ctx["old"] + "# existing local change\n").encode()
        )
    exports = Path(tempfile.mkdtemp(prefix="srg1-case-"))
    await runtime.configure_local_git_review(
        task.id,
        task.actor,
        executable=ctx["git"],
        executable_sha256="cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96",
        export_parent=exports,
    )
    if mode in {"deleted_input", "renamed_input"}:
        factory = runtime._executor

        def executor(metadata):
            instance = factory(metadata)
            agent = instance._agent

            async def changed_source(*args):
                output = await agent(*args)
                source = runtime._frozen_sandboxes[task.id][0].root / "src/app.py"
                if mode == "deleted_input":
                    source.unlink()
                else:
                    source.rename(source.with_name("renamed.py"))
                return output

            instance._agent = changed_source
            return instance

        monkeypatch.setattr(runtime, "_executor", executor)
    original_prepare = runtime._prepare_local_apply_proposal
    observations = {}

    async def publishing(frame):
        state = runtime._local_git_reviews[task.id]
        view = state["source"]
        receipt = next(r for r in runtime._local_acceptance_receipts if r.task_id == task.id)
        assert runtime._local_acceptance_receipts[receipt][0]["guard_state"] == "completed"
        assert task.id not in runtime._verification_hosts
        before = {name: (view.metadata / name).read_bytes().hex() for name in ("HEAD", "index")}
        if mode == "baseline":
            ref = (view.metadata / "HEAD").read_text().strip()
            assert ref.startswith("ref: ")
            (view.common / ref[5:]).write_bytes(b"0" * 40 + b"\n")
        if mode == "forged_receipt":
            binder = runtime.bind_task_git_view

            async def forged(*args, **kwargs):
                kwargs["local_receipt"] = copy(kwargs["local_receipt"])
                return await binder(*args, **kwargs)

            monkeypatch.setattr(runtime, "bind_task_git_view", forged)
        observations.update(receipt_sha256=receipt.payload_sha256, before=before)
        result = await original_prepare(frame)
        assert before == {
            name: (view.metadata / name).read_bytes().hex() for name in ("HEAD", "index")
        }
        observations["review"] = state["evidence"]
        return result

    monkeypatch.setattr(runtime, "_prepare_local_apply_proposal", publishing)
    try:
        result = await runtime.execute(task)
        nodes = await store.list_execution_nodes(task.id)
        proposals = await store.list_change_proposals(task.id)
        if mode == "baseline_after_review":
            from Sprout.gateway.rpc_gateway import RPCGateway

            assert len(proposals) == 1
            view = runtime._local_git_reviews[task.id]["git"]
            ref = (view.metadata / "HEAD").read_text().strip()
            (view.common / ref[5:]).write_bytes(b"0" * 40 + b"\n")
            await RPCGateway(runtime).handle(
                {"id": 1, "method": "proposal.approve", "params": {"proposal_id": proposals[0].id}},
                principal=task.actor,
            )
            nodes = await store.list_execution_nodes(task.id)
        approval = next((n for n in nodes if n.type.value == "approval"), None)
        report = {
            "mode": mode,
            "task": task.id,
            "result": str(result),
            "nodes": [
                {"type": n.type.value, "status": n.status.value, "metadata": n.metadata}
                for n in nodes
            ],
            "observations": observations,
            "exports": str(exports),
        }
        (tmp_path / "LOCAL_G1.json").write_text(json.dumps(report, default=str, indent=2))
        if mode in {"deleted_input", "renamed_input"}:
            assert not proposals and not runtime._local_acceptance_receipts
            evaluation = next(n for n in nodes if n.type.value == "evaluation")
            assert (
                evaluation.metadata["output"]["reason"]
                == "local_git_unsupported_missing_input:src/app.py"
            )
            assert evaluation.metadata["output"]["git_diff"]["status"] == "not_executed"
            assert not list(exports.glob("git-review-diagnostic-*.json"))
            assert (ctx["root"] / "src/app.py").read_bytes() == ctx["old"].encode()
            return
        assert approval is not None, report
        assert (ctx["root"] / "src/app.py").read_bytes() == (
            ctx["old"] + ("# existing local change\n" if mode == "dirty_target" else "")
        ).encode()
        if mode in {"success", "dirty_target"}:
            assert len(proposals) == 1
            assert isinstance(runtime._local_git_reviews[task.id]["git"], _ReviewView)
            assert proposals[0].metadata["git_diff_status"] == "available"
            diagnostic = json.loads(next(exports.glob("git-review-diagnostic-*.json")).read_text())
            assert not diagnostic["unresolved_processes"]
            pids = [
                x["pid"]
                for x in diagnostic["trace"]
                if x["phase"] == "command" and x["state"] == "exited"
            ]
            assert len(pids) == 5 and all(p > 0 for p in pids)
            if mode == "dirty_target":
                from Sprout.gateway.rpc_gateway import RPCGateway

                assert "-# existing local change" in proposals[0].metadata["content_diff"]["text"]
                assert (
                    "existing local change"
                    not in proposals[0].metadata["git_diff"]["patches"]["tracked"]
                )
                response = await RPCGateway(runtime).handle(
                    {
                        "id": 2,
                        "method": "proposal.approve",
                        "params": {"proposal_id": proposals[0].id},
                    },
                    principal=task.actor,
                )
                assert "error" not in response
                final = await store.get_change_proposal(proposals[0].id)
                assert final.status.value == "applied"
                assert (ctx["root"] / "src/app.py").read_bytes() == final.metadata["apply_plan"][
                    "files"
                ][0]["new_text"].encode()

        elif mode == "untracked":
            assert not proposals
            output = approval.metadata["output"]
            assert output["status"] == "apply_unsupported"
            assert output["unsupported_operations"] == [
                {"path": "src/new.py", "operation": "added"}
            ]
            assert output["git_diff"]["status"] == "available"
        elif mode == "baseline_after_review":
            assert "git_baseline_changed" in approval.metadata["error"]
            assert len(list(exports.glob("git-review-diagnostic-*.json"))) == 1
            assert not await store._db.fetchall(
                "SELECT id FROM isolated_git_transactions WHERE operation='apply_v1'"
            )
        else:
            assert not proposals
            expected = (
                "git_baseline_changed" if mode == "baseline" else "local_apply_receipt_unavailable"
            )
            assert expected in approval.metadata["error"]
    finally:
        await runtime.stop()
        assert not runtime._local_git_reviews
        store.close()
