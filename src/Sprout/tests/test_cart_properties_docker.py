"""Real independent candidate checks using existing Docker/SQLite session plumbing."""

import json
import os
from uuid import NAMESPACE_URL, uuid5

import pytest

from Sprout.execution.coding_session import CodingNodeSession
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.models import SandboxRef
from Sprout.execution.process_broker import ProcessBroker
from Sprout.orchestration.models import ExecutionNode, NodeType
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus
from Sprout.tests.acceptance_samples import sample_definition
from Sprout.tests.test_cart_acceptance_fixture import REFERENCE
from Sprout.tests.test_coding_docker_integration import (
    FixturePolicy,
    inspect_and_remove_owned_stopped,
)
from Sprout.workspace.models import Workspace


@pytest.mark.parametrize("variant", ["baseline", "reference", "mutating", "inline", "stateful"])
async def test_independent_cart_properties_and_json_oracles(tmp_path, variant):
    image = os.getenv("SPROUT_CODING_TEST_IMAGE")
    if not image:
        pytest.skip("Explicit local pinned Docker image required; no model API")
    sample = sample_definition("cart")
    original, target, evidence = (tmp_path / name for name in ("original", "target", "evidence"))
    for root in (original, target, evidence):
        root.mkdir()
    sources = {**sample["sources"], **(REFERENCE if variant != "baseline" else {})}
    if variant == "mutating":
        sources["checkout.py"] += (
            "\n_original_quote = quote\ndef quote(items):\n"
            "    result = _original_quote(items)\n"
            "    if isinstance(items, list): items.clear()\n    return result\n")
    elif variant == "stateful":
        sources["checkout.py"] += (
            "\n_original_quote = quote\n_calls = 0\ndef quote(items):\n"
            "    global _calls\n    _calls += 1\n    result = _original_quote(items)\n"
            "    if 'total_cents' in result: result['total_cents'] += _calls\n"
            "    return result\n")
    elif variant == "inline":
        sources["checkout.py"] = (
            "def quote(items):\n"
            "    amount = sum(i['price_cents'] * i['quantity'] for i in items)\n"
            "    discount = 1500 if amount >= 10000 else 0\n"
            "    return dict(subtotal_cents=amount, discount_cents=discount, "
            "total_cents=amount-discount)\n")
    for name, content in sources.items():
        (target / name).write_text(content, encoding="utf-8")
    (target / "oracles").mkdir()
    (target / "oracles/frozen.txt").write_text("host only", encoding="utf-8")
    store = open_metadata_store(str(evidence / "metadata.db"))
    task = Task(status=TaskStatus.RUNNING, metadata={"execution_mode": "code"})
    await store.save_task(task)
    node = ExecutionNode(task_id=task.id, type=NodeType.EVALUATION)
    await store.save_execution_node(node)
    assert await store.claim_execution_node(node, "owner", "")
    node = await store.get_execution_node(node.id)
    policy = FixturePolicy()
    broker = ProcessBroker(policy)
    workspace = Workspace(id="trial", root=target)

    class Runner:
        async def run_task_process(self, task_id, command, **kwargs):
            return await broker.run(workspace, command, task_id=task_id, **kwargs)

        async def authorize_task_process(self, task_id, command):
            return await broker.run(workspace, command, task_id=task_id, preflight_only=True)

    session = CodingNodeSession(store, Runner(), FileBroker(policy), evidence=evidence,
        original=original, image=image, oracle_paths=("oracles",),
        sandbox=SandboxRef("trial", "fixture", target), task=task, node=node)
    identities = [str(uuid5(NAMESPACE_URL, node.id + ":coding:auxiliary-0"))]
    result = {}
    try:
        result = await session.verify_auxiliary({
            "verification_commands": sample["verification_commands"]})
        assert result["verified"] is (variant == "reference")
        if variant in {"baseline", "reference"}:
            for kind, count in (("acceptance", 3), ("regression", 17), ("poc", 1)):
                identities.extend(str(uuid5(NAMESPACE_URL,
                    node.id + f":coding:evaluation-{kind}-{index}")) for index in range(count))
            measured = await session.verify(acceptance_spec=sample["acceptance"],
                                            regression_spec=sample["regression"])
            assert measured["verified"] is (variant == "reference")
            assert len(measured["results"]) == 21
        assert (target / "oracles/frozen.txt").read_text() == "host only"
    finally:
        (evidence / "property-result.json").write_text(json.dumps(result, indent=2),
                                                       encoding="utf-8")
        try:
            for identity in identities:
                record = await store.get_coding_execution(identity)
                if record and record["state"] == "completed":
                    command = record["command"]
                    inspect_and_remove_owned_stopped(command[command.index("--name") + 1], evidence)
        finally:
            store.close()
