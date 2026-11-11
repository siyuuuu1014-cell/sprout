"""Same-frame approval lifecycle, real metadata; OS/process are controlled substitutes."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from Sprout.execution.acceptance import local_executor_binding
from Sprout.orchestration.models import NodeBudget
from Sprout.runtime.nodes import NodeExecutor
from Sprout.security.access import AccessDecision
from Sprout.tests.test_r04_f2_execution_contract import execution as execution
from Sprout.tests.test_r04_f2_execution_contract import rig as rig


async def invoke(e):
    executor = SimpleNamespace(_isolated_coding_required=False, _local_acceptance=True,
                               _process_broker=e.broker)
    return await NodeExecutor._local_acceptance_run(executor, e.rig.workspace, e.rig.task,
        e.root, e.spec, "tests/frozen.py", "normal", local_executor_binding())


@pytest.mark.parametrize("decision", ["approve", "reject", "expired", "task", "owner",
    "fingerprint", "scope", "policy", "cancel", "timeout", "missing-budget",
    "expired-contract", "source", "session", "resource", "action-hash"])
async def test_same_runtime_wait(execution, monkeypatch, decision):
    e = execution
    e.mode[0] = AccessDecision.REQUIRE_APPROVAL
    # The live Guard remains unchanged; shorten only the fixture's handler budget.
    budget = 5.01 if decision == "timeout" else 30
    if decision != "missing-budget":
        e.rig.node = replace(e.rig.node, budget=NodeBudget(timeout_seconds=budget))
    manager = e.broker._approvals
    request = manager.request
    records = []

    async def requested(*args, **kwargs):
        record = await request(*args, **kwargs)
        records.append(record)
        e.spawn.assert_not_called()
        return record
    monkeypatch.setattr(manager, "request", requested)
    wait = e.broker._wait_frozen_approval

    async def human_wait(record, approval_id):
        assert record["used"]
        assert not e.rig.runtime._frozen_executions  # old permit already disposed
        e.spawn.assert_not_called()
        if decision == "cancel":
            raise asyncio.CancelledError
        if decision == "expired":
            stored = await manager.store.get_approval(approval_id)
            await manager.store.save_approval(replace(stored,
                expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        if decision in {"task", "fingerprint", "source", "session", "resource", "action-hash"}:
            stored = await manager.store.get_approval(approval_id)
            field = {"task": "task_id", "fingerprint": "arguments_fingerprint",
                "source": "source", "session": "session_id", "resource": "resource_scope",
                "action-hash": "action_hash"}[decision]
            await manager.store.save_approval(replace(stored, **{field: "wrong"}))
        if decision == "scope":
            await e.rig.storage.metadata.save_task(replace(e.rig.task,
                delegation_scope=replace(e.rig.task.delegation_scope,
                    denied_actions=frozenset({"process.run"}))))
        if decision == "owner":
            original = e.rig.storage.metadata.get_execution_node

            async def other_owner(node_id):
                node = await original(node_id)
                if node and node.id == e.rig.node.id:
                    return replace(node, metadata={**node.metadata, "execution_guard": {
                        **node.metadata["execution_guard"], "owner": "other"}})
                return node
            monkeypatch.setattr(e.rig.storage.metadata, "get_execution_node", other_owner)
        if decision in {"approve", "policy", "expired-contract"}:
            # Only unit tests simulate a human. Actual CLI never invokes decide.
            await manager.decide(approval_id, True, decided_by="fixture-human")
            if decision == "policy":
                e.mode[0] = AccessDecision.DENY
            if decision == "expired-contract":
                record["expires"] = 0  # discarded permit cannot survive the wait
        if decision == "reject":
            await manager.decide(approval_id, False, decided_by="fixture-human")
        await wait(record, approval_id)
    monkeypatch.setattr(e.broker, "_wait_frozen_approval", human_wait)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        if decision == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await invoke(e)
        else:
            result = await invoke(e)
            assert result.allowed is (decision in {"approve", "expired-contract"})
            if decision not in {"approve", "expired-contract"}:
                assert result.started is False
    assert len(records) == 1
    assert e.spawn.await_count == (1 if decision in {"approve", "expired-contract"} else 0)


async def test_pending_wait_is_async_and_cancellable(execution):
    e = execution
    e.mode[0] = AccessDecision.REQUIRE_APPROVAL
    e.rig.node = replace(e.rig.node, budget=NodeBudget(timeout_seconds=30))
    current = asyncio.current_task()

    async def cancel_later():
        await asyncio.sleep(0.05)
        e.spawn.assert_not_called()
        current.cancel()
    helper = asyncio.create_task(cancel_later())
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        with pytest.raises(asyncio.CancelledError):
            await invoke(e)
    await helper
    e.spawn.assert_not_called()


async def test_human_decision_arrives_while_original_frame_waits(execution, monkeypatch):
    e = execution
    e.mode[0] = AccessDecision.REQUIRE_APPROVAL
    e.rig.node = replace(e.rig.node, budget=NodeBudget(timeout_seconds=30))
    manager = e.broker._approvals
    request = manager.request
    pending = asyncio.Future()

    async def requested(*args, **kwargs):
        record = await request(*args, **kwargs)
        pending.set_result(record)
        return record
    monkeypatch.setattr(manager, "request", requested)

    async def human():
        record = await pending
        await asyncio.sleep(0.03)
        e.spawn.assert_not_called()
        await manager.decide(record.id, True, decided_by="fixture-human")
    helper = asyncio.create_task(human())
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        result = await invoke(e)
    await helper
    assert result.started and result.exit_code == 0
    assert e.spawn.await_count == 1


@pytest.mark.parametrize("choice", ["approve", "reject"])
async def test_foreground_cli_prompts_for_in_node_approval(execution, monkeypatch, choice):
    from unittest.mock import AsyncMock

    from Sprout.cli.commands import chat
    from Sprout.message.models import Message, StreamChunk

    e = execution
    e.mode[0] = AccessDecision.REQUIRE_APPROVAL
    e.rig.node = replace(e.rig.node, budget=NodeBudget(timeout_seconds=30))
    runtime, principal = e.rig.runtime, e.rig.principal
    prompt = AsyncMock(return_value=choice)
    monkeypatch.setattr(chat, "_prompt_task_gate_choice", prompt)
    monkeypatch.setattr(chat, "_print_approval_record", lambda record: None)

    async def stream(message, **kwargs):
        with runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
            result = await invoke(e)
        yield StreamChunk(content=str(result.allowed))

    monkeypatch.setattr(runtime, "handle_stream", stream)
    message = Message("run", channel="cli", session_id=e.rig.session.id)

    async def collect():
        return [c.content async for c in chat._cli_turn_stream(
            runtime, message, principal=principal, foreground=True)]

    assert await asyncio.wait_for(collect(), timeout=4) == [str(choice == "approve")]
    prompt.assert_awaited_once()
    assert e.spawn.await_count == int(choice == "approve")
