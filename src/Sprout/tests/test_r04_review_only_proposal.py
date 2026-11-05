"""File SQLite + real Local graph/Guard/NodeExecutor; only execution is substituted."""

import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest

from Sprout.execution.verification_manifest import digest
from Sprout.orchestration.models import (
    ExecutionGraph,
    ExecutionNode,
    NodeBudget,
    NodeStatus,
    NodeType,
)
from Sprout.orchestration.service import SequentialOrchestrator
from Sprout.security.access import AccessDecision
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import TaskStatus
from Sprout.tests.test_r04_f3_claim import record
from Sprout.tests.test_r04_f3_manifest import execution as execution
from Sprout.tests.test_r04_f3_manifest import f3 as f3
from Sprout.tests.test_r04_f3_manifest import rig as rig
from Sprout.workspace.models import ReadPlan


@pytest.fixture
async def graph(f3, monkeypatch):
    e = f3
    runtime = e.rig.runtime
    old = runtime.storage.metadata
    evidence = e.root.parent / "evidence"
    evidence.mkdir()
    store = open_metadata_store(evidence / "review-graph.db")

    # Real SQLite statements/transactions; inline scheduling avoids relying on
    # the no-socket launcher's absent cross-thread wakeup pipe. No SQL is mocked.
    async def fetchone(sql, parameters=()):
        return store._db._read("fetchone", sql, parameters)

    async def fetchall(sql, parameters=()):
        return store._db._read("fetchall", sql, parameters)

    async def execute(sql, parameters=()):
        return store._db._sync_execute(sql, parameters)

    async def batch(statements):
        return store._db._sync_execute_batch(list(statements))

    monkeypatch.setattr(store._db, "fetchone", fetchone)
    monkeypatch.setattr(store._db, "fetchall", fetchall)
    monkeypatch.setattr(store._db, "execute", execute)
    monkeypatch.setattr(store._db, "execute_batch", batch)
    await store.save_workspace(e.rig.workspace)
    # Initial record in this new database, not a reset of the source fixture's
    # RUNNING task or any persisted owner. Local dispatch requires READY.
    await store.save_task(replace(e.rig.task, status=TaskStatus.READY))
    parents = [
        n for n in await old.list_execution_nodes(e.rig.task.id) if n.status is NodeStatus.COMPLETED
    ]
    # Copy existing, genuine fixture settlements and side records, not invented
    # NodeResultSource credentials. Never reset its already-running old node.
    for node in parents:
        await store.save_execution_node(node)
        source = await old.get_node_result_source(node.id)
        if source is not None:
            assert await store.save_node_result_source(source)
    runtime.storage.metadata = store
    host = runtime._verification_hosts[e.rig.task.id]
    tools = e.root.parent / "host-tools"
    tools.mkdir()
    cli = tools / "docker-placeholder"
    cli.write_bytes(b"unit identity only, never executed")
    host.update(
        docker={
            "path": str(cli),
            "sha256": hashlib.sha256(cli.read_bytes()).hexdigest(),
            "context": "unit-local",
            "endpoint": "npipe://unit",
            "daemon_id": "unit-daemon",
        },
        discovery={
            "confirmed": True,
            "test_files": ["tests/frozen.py"],
            "config_files": [],
            "plugins": [],
            "plugin_autoload": False,
        },
        tools={
            "python": {"path": "/usr/local/bin/python3", "sha256": "a" * 64},
            "pytest": {"path": "/usr/local/bin/pytest", "sha256": "b" * 64, "plugins": []},
            "ruff": {
                "path": "/usr/local/bin/ruff",
                "sha256": "c" * 64,
                "configuration_confirmed": True,
                "config_files": [],
            },
        },
    )
    agent = next(n for n in parents if n.type is NodeType.AGENT and n.id == "f3-agent")
    evaluation = ExecutionNode(
        id="review-evaluation",
        task_id=e.rig.task.id,
        type=NodeType.EVALUATION,
        dependencies=(agent.id,),
        budget=NodeBudget(timeout_seconds=300),
        metadata={
            "test_commands": ["pytest"],
            "verification_commands": [{"command": "pytest -q", "required": True}],
        },
    )
    approval = ExecutionNode(
        id="review-approval",
        task_id=e.rig.task.id,
        type=NodeType.APPROVAL,
        dependencies=(evaluation.id,),
        budget=NodeBudget(timeout_seconds=30),
    )
    flow = ExecutionGraph(task_id=e.rig.task.id, nodes=(*parents, evaluation, approval))
    calls, decisions, mode = [], [], ["unverified"]
    broker = e.broker

    async def controlled_execution(manifest, issuer, scope, exported, slot, **kwargs):
        prepared = await broker.prepare_verification(manifest, issuer, scope, exported)
        row = prepared[slot]
        if row["reason"] != "verification_native_commissioning_required":
            return {
                "outcome": row["outcome"],
                "reason": row["reason"],
                "executed": False,
                "passed": False,
            }
        if mode[0] == "not-executed":
            return {
                "outcome": "not_executed",
                "reason": "unit executor unavailable",
                "executed": False,
                "passed": False,
            }
        arguments = broker._verification_obligation_arguments(manifest, issuer, slot)
        request = await broker._approvals.request(
            "cli_tool_run",
            arguments,
            task_id=e.rig.task.id,
            requested_by=e.rig.task.actor.user_id,
            source=e.rig.task.source,
            session_id=scope.session_id,
            resource_scope=e.rig.workspace.id,
        )
        decisions.append(request.id)
        if mode[0] == "pending":
            return {
                "outcome": "approval_required",
                "approval_id": request.id,
                "executed": False,
                "passed": False,
            }
        await broker._approvals.decide(request.id, True, decided_by="controlled-unit-human")
        assert await broker._approvals.is_approved(
            "cli_tool_run",
            arguments,
            task_id=e.rig.task.id,
            requested_by=e.rig.task.actor.user_id,
            source=e.rig.task.source,
            session_id=scope.session_id,
            resource_scope=e.rig.workspace.id,
        )
        node = await store.get_execution_node(scope.node.id)
        task = await store.get_task(e.rig.task.id)
        saved = record(
            SimpleNamespace(rig=SimpleNamespace(task=task, node=node, workspace=e.rig.workspace))
        )
        saved.update(
            id=str(uuid5(NAMESPACE_URL, task.id + ":ordinary:" + node.id + ":" + slot)),
            manifest=manifest.sha256,
            slot=slot,
            image=row["prepared"]["image"],
            signature=digest(row["prepared"]),
            approval_id=request.id,
        )
        assert await store.claim_verification_execution(saved)
        pytest_result = slot in {"project-pytest", "strategy-pytest"} or slot.startswith(
            "acceptance:"
        )
        outcome = "unverified" if pytest_result else "passed"
        if mode[0] == "failed" and slot == "ruff":
            outcome = "check_failed"
        if mode[0] == "unknown":
            outcome = "UNKNOWN"
        result = {
            "outcome": outcome,
            "passed": outcome == "passed",
            "executed": outcome != "UNKNOWN",
            "exit_code": None if outcome == "UNKNOWN" else 1 if outcome == "check_failed" else 0,
            "stdout": "controlled executor output:" + slot,
            "stderr": "",
            "duration_ms": 1,
            "execution_id": saved["id"],
            "container_id": hashlib.sha256(slot.encode()).hexdigest(),
            "retry_allowed": False,
        }
        assert await store.finish_verification_execution(
            saved["id"],
            saved["owner"],
            "uncertain" if outcome == "UNKNOWN" else "completed",
            result,
        )
        calls.append(slot)
        if mode[0] == "cleanup":
            runtime._verification_preparations[manifest]["native"] = {
                "manual_intervention_required": True
            }
        return result

    monkeypatch.setattr(broker, "execute_verification_obligation", controlled_execution)
    native_git = AsyncMock(side_effect=AssertionError("No legacy Git IO for review-only"))
    monkeypatch.setattr("Sprout.runtime.nodes.GitWorktreeSandbox.diff", native_git)
    monkeypatch.setattr("Sprout.runtime.nodes.GitWorktreeSandbox.changed_files", native_git)
    handler = runtime._executor(store).handler(
        e.rig.task, e.rig.workspace, ReadPlan(task_id=e.rig.task.id)
    )
    runner = SequentialOrchestrator(store)
    import asyncio

    async def tick():
        while True:
            await asyncio.sleep(0)

    ticker = asyncio.create_task(tick())
    try:
        yield SimpleNamespace(**locals())
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
        runtime.storage.metadata = old
        store.close()


@pytest.mark.parametrize("mode", ["unverified", "failed", "policy-deny", "not-executed"])
async def test_settled_collection_reaches_persistent_review_proposal(graph, mode):
    g = graph
    g.mode[0] = mode
    if mode == "policy-deny":
        g.e.mode[0] = AccessDecision.DENY
    result = await run_graph(g)
    proposals = await g.store.list_change_proposals(g.e.rig.task.id)
    assert len(proposals) == 1, result.status
    proposal = proposals[0]
    assert proposal.metadata["review_only"] is True
    assert proposal.metadata["git_diff_status"] == "git_diff_unavailable"
    assert proposal.files_changed == ("src/app.py",)
    assert not proposal.diffs  # content comparisons must not impersonate native Git output
    assert "changed" in proposal.metadata["content_diff"]["text"]
    report = proposal.metadata["verification"]["verification_report"]
    assert report["verified"] is False
    assert proposal.metadata["verification"]["collection_complete"]
    nodes = {n.id: n for n in await g.store.list_execution_nodes(g.e.rig.task.id)}
    assert nodes[g.evaluation.id].status is NodeStatus.COMPLETED
    assert nodes[g.evaluation.id].metadata["execution_guard"]["state"] == "completed"
    assert nodes[g.approval.id].status is NodeStatus.WAITING
    g.native_git.assert_not_called()
    if mode == "failed":
        assert proposal.metadata["verification"]["failed_commands"] == ["check: ruff"]
    elif mode in {"policy-deny", "not-executed"}:
        assert not g.calls and not g.decisions
        expected = "policy_denied" if mode == "policy-deny" else "not_executed"
        assert all(c["outcome"] == expected for c in report["checks"])
    else:
        assert len(g.calls) == 6
        assert len(proposal.metadata["verification"]["unverified_commands"]) == 3
        assert not proposal.metadata["verification"]["failed_commands"]


async def run_graph(g, handler=None):
    return await g.runner.execute(g.flow, handler=handler or g.handler)


@pytest.mark.parametrize("mode", ["pending", "unknown", "cleanup"])
async def test_unsettled_collection_does_not_publish(graph, mode):
    g = graph
    g.mode[0] = mode
    await run_graph(g)
    assert not await g.store.list_change_proposals(g.e.rig.task.id)
    nodes = {n.id: n for n in await g.store.list_execution_nodes(g.e.rig.task.id)}
    assert nodes[g.evaluation.id].status is not NodeStatus.COMPLETED
    assert g.approval.id not in nodes  # Never scheduled or claimed.
    if mode == "pending":
        assert not g.calls
    g.native_git.assert_not_called()


@pytest.mark.parametrize(
    "tamper", ["owner", "runtime", "receipt", "output", "task", "policy", "binding"]
)
async def test_review_publication_requires_live_settled_source(graph, tamper):
    g = graph

    async def handle(node):
        if node.type is NodeType.APPROVAL:
            host = g.runtime._verification_hosts[g.e.rig.task.id]
            receipt = host["review_results"][g.evaluation.id]
            if tamper == "binding":
                g.runtime._verification_hosts.clear()
            elif tamper == "receipt":
                host["review_results"].clear()
            elif tamper == "policy":
                from Sprout.security.access import PolicyDecision

                original = g.runtime._file_broker._policy.decide

                def deny(request):
                    return (
                        PolicyDecision(AccessDecision.DENY, "revoked before publication")
                        if request.action.value == "file.read"
                        else original(request)
                    )

                g.monkeypatch.setattr(g.runtime._file_broker._policy, "decide", deny)
            elif tamper == "task":
                current = await g.store.get_task(g.e.rig.task.id)
                await g.store.save_task(
                    replace(current, actor=replace(current.actor, user_id="other"))
                )
            else:
                receipt[tamper] = object() if tamper == "runtime" else "changed"
        return await g.handler(node)

    await run_graph(g, handle)
    assert not await g.store.list_change_proposals(g.e.rig.task.id)
    g.native_git.assert_not_called()


async def test_approved_review_proposal_stays_unverified_and_cannot_apply(graph):
    from Sprout.execution.verification import proposal_verification_blocker
    from Sprout.runtime.changes import ChangeProposalService

    g = graph
    await run_graph(g)
    (proposal,) = await g.store.list_change_proposals(g.e.rig.task.id)
    apply = AsyncMock(side_effect=AssertionError("No actual Apply"))
    service = ChangeProposalService(
        metadata=g.store,
        operational=g.runtime.storage.operational,
        approvals=g.broker._approvals,
        apply_broker=apply,
    )
    approved = await service.approve(proposal.id, decided_by="controlled-unit-human")
    assert approved.metadata == proposal.metadata
    assert proposal_verification_blocker(approved)
    result = await service.apply(proposal.id)
    assert result.applied is False
    apply.assert_not_called()
    # Independent read-only SQLite connection verifies the persisted data.
    import sqlite3

    path = g.evidence / "review-graph.db"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        saved = connection.execute(
            "SELECT metadata_json FROM change_proposals WHERE id=?", (proposal.id,)
        ).fetchone()
        assert saved is not None and "unverified" in saved[0]
