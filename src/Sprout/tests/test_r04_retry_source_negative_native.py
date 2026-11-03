"""Negative provenance checks in fresh native Local Runtime instances."""

import json
import os
from copy import copy
from dataclasses import replace

import pytest

from Sprout.orchestration.compiler import ExecutionGraphBuilder
from Sprout.orchestration.models import NodeType
from Sprout.tests.test_r04_e_apply_native import prepare_apply_fixture

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Explicit Windows native entry required",
)


@pytest.mark.parametrize("mutation", [
    "owner", "session", "task", "sandbox", "unregistered_tool", "forged_output",
])
async def test_unproved_sandbox_result_has_no_source(tmp_path, monkeypatch, mutation):
    ctx = await prepare_apply_fixture(tmp_path, monkeypatch, "cli")
    runtime, store, task = ctx["runtime"], ctx["storage"].metadata, ctx["task"]
    runtime._graph_builder = ExecutionGraphBuilder()
    track = runtime._track_sandbox_result
    observed = []

    async def changed_subject(scope, tool, arguments, result):
        original = (scope.owner, scope.session_id, scope.task)
        registered = runtime._frozen_sandboxes[task.id]
        try:
            if mutation == "owner":
                scope.owner = None
            elif mutation == "session":
                scope.session_id = "wrong-session"
            elif mutation == "task":
                scope.task = replace(task, id="other-task")
            elif mutation == "sandbox":
                runtime._frozen_sandboxes[task.id] = (
                    replace(registered[0], id="other-sandbox"), *registered[1:])
            elif mutation == "unregistered_tool":
                tool = copy(tool)
            else:
                # Even a completion-looking output cannot clear the private
                # untracked-effects marker or create a persisted source.
                scope.sandbox_tool_results = ({"verified": True, "owner": scope.guard_owner},)
                observed.append("unproved")
                return False
            try:
                value = await track(scope, tool, arguments, result)
            except PermissionError as exc:
                observed.append(str(exc))
                raise
            assert value is False
            observed.append("rejected")
            return value
        finally:
            scope.owner, scope.session_id, scope.task = original
            runtime._frozen_sandboxes[task.id] = registered

    monkeypatch.setattr(runtime, "_track_sandbox_result", changed_subject)
    try:
        result = await runtime._execute_task(task, artifact_snapshot=None)
        nodes = await store.list_execution_nodes(task.id)
        agents = [n for n in nodes if n.type is NodeType.AGENT]
        assert observed and agents
        for agent in agents:
            assert await store.get_node_result_source(agent.id) is None
        assert not await store.list_change_proposals(task.id)
        assert not runtime._local_acceptance_receipts
        assert (ctx["root"] / "src/app.py").read_text() == ctx["old"]
        (tmp_path / "NEGATIVE_SOURCE.json").write_text(json.dumps({
            "mutation": mutation, "result": str(result), "observed": observed,
            "nodes": [{"id": n.id, "status": n.status.value, "metadata": n.metadata}
                      for n in nodes],
        }, default=str, indent=2))
    finally:
        await runtime.stop()
        store.close()
