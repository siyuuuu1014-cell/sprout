"""Real started processes keep both command and active-node timeout handling."""
import asyncio
import json
import os
from dataclasses import replace

import pytest

from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeBudget, NodeType
from Sprout.orchestration.service import SequentialOrchestrator
from Sprout.tests.test_r04_e_apply_native import prepare_apply_fixture

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Explicit Windows native entry required",
)


@pytest.mark.parametrize("mode", ["command", "node"])
async def test_started_process_timeout_is_not_an_approval_wait(tmp_path, monkeypatch, mode):
    ctx = await prepare_apply_fixture(tmp_path, monkeypatch, "cli")
    runtime, store, task = ctx["runtime"], ctx["storage"].metadata, ctx["task"]
    task = replace(task, delegation_scope=replace(
        task.delegation_scope, allowed_paths=(str(ctx["root"]),)))
    await store.save_task(task)
    node = ExecutionNode(task_id=task.id, type=NodeType.EVALUATION,
                         budget=NodeBudget(timeout_seconds=1 if mode == "node" else 10))
    graph = ExecutionGraph(task_id=task.id, nodes=(node,), entry_node_ids=(node.id,))
    processes, outcomes, calls = [], [], []
    original = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    async def handler(running):
        calls.append(running.id)
        outcome = await runtime._process_broker.run(
            ctx["workspace"], ("python", "-c", "import time; time.sleep(20)"),
            cwd=ctx["root"], task_id=task.id, scope=task.delegation_scope,
            actor=task.actor, source=task.source, requested_by=task.actor.user_id,
            session_id=task.metadata["session_id"], resource_scope=task.workspace_id,
            timeout_seconds=.2 if mode == "command" else 10)
        outcomes.append(outcome)
        assert outcome.allowed and outcome.exit_code is None
        assert outcome.error == "Process timed out"
        return {"waiting": True, "waiting_kind": "resource", "reason": outcome.error}

    runner = SequentialOrchestrator(store)
    try:
        async with asyncio.timeout(15):
            await runner.execute(graph, handler=handler)
        assert len(processes) == 1
        assert processes[0].returncode is not None
        settled = await store.get_execution_node(node.id)
        assert settled.metadata["execution_guard"]["state"] == "uncertain"
        if mode == "node":
            assert not outcomes and "TimeoutError" in settled.metadata["error"]
        else:
            assert len(outcomes) == 1
        await runner.execute(graph, handler=handler)
        assert calls == [node.id]
        (tmp_path / "STARTED_TIMEOUT.json").write_text(json.dumps({
            "pid": processes[0].pid, "returncode": processes[0].returncode,
            "node": settled.metadata, "mode": mode}, default=str, indent=2))
    finally:
        await runtime.stop()
        store.close()
