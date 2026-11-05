"""Real Windows Receipt/G1/SQLite; terminal answers are explicitly simulated."""
import hashlib
import json
import os
from uuid import NAMESPACE_URL, uuid5

import pytest

from Sprout.cli.commands import chat
from Sprout.tests.test_r04_e_apply_native import prepare_apply_fixture

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Windows native Receipt/Apply commissioning required",
)


async def test_final_plan_inputs_real_files_and_transactions(tmp_path, monkeypatch):
    ctx = await prepare_apply_fixture(tmp_path, monkeypatch, "single")
    runtime, task, root = ctx["runtime"], ctx["task"], ctx["root"]
    evidence = []
    try:
        await runtime._execute_task(task, artifact_snapshot=None)
        proposal, = await runtime.list_change_proposals(task.id)
        digest = proposal.metadata["apply_plan_sha256"]
        before = hashlib.sha256((root / "src/app.py").read_bytes()).hexdigest()
        transaction = str(uuid5(NAMESPACE_URL, "ordinary-apply:" + proposal.id))
        monkeypatch.setattr(chat, "current_cli_principal", lambda: ctx["principal"])
        initial_approvals = [a.id for a in await runtime.approvals.store.list_approvals()]

        class Prompt:
            async def ask_async(self):
                return answer

        monkeypatch.setattr(chat.questionary, "text", lambda *a, **k: Prompt())
        # Empty includes a leftover Return; no input is a product approval.
        for answer in ("", "y", "approve", "APPLY deadbeef", None):
            await chat._approve_task_gates(runtime, task, "display-only")
            row = await runtime.storage.metadata.get_apply_transaction(transaction)
            actual = hashlib.sha256((root / "src/app.py").read_bytes()).hexdigest()
            assert actual == before
            assert row is None
            assert (await runtime.find_change_proposal(proposal.id)).status.value == "pending"
            approvals = await runtime.approvals.store.list_approvals()
            assert [a.id for a in approvals] == initial_approvals
            evidence.append({"simulated_answer": answer, "sha256": actual,
                             "transaction": row, "proposal": "pending"})

        answer = f"APPLY {digest[:8]}"  # Simulated test user, not real-user authorization.
        await chat._approve_task_gates(runtime, task, "display-only")
        row = await runtime.storage.metadata.get_apply_transaction(transaction)
        assert row["phase"] == "COMPLETED"
        assert (root / "src/app.py").read_text() == ctx["new"]
        assert (await runtime.find_change_proposal(proposal.id)).status.value == "applied"
        approvals = await runtime.approvals.store.list_approvals()
        apply_approvals = [a for a in approvals if a.tool == "file.apply"]
        assert len(apply_approvals) == 1 and apply_approvals[0].status.value == "consumed"
        evidence.append({"simulated_answer": answer, "transaction": row,
                         "sha256": hashlib.sha256((root / "src/app.py").read_bytes()).hexdigest(),
                         "approval_ids": [a.id for a in apply_approvals]})
    finally:
        (tmp_path / "FINAL_CONFIRMATION_EVIDENCE.json").write_text(
            json.dumps(evidence, default=str, indent=2), encoding="utf-8")
        await runtime.stop()
        await runtime.storage.close()
