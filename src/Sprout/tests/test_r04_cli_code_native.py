"""Native CODE preparation → real Receipt/G1 → CLI exact-plan test approval.

Model and user input are controlled; OS identity, Git, files, Guard and SQLite are real.
"""
import asyncio
import json
import os
from uuid import NAMESPACE_URL, uuid5

import pytest

from Sprout.cli import code_task
from Sprout.cli.commands import chat
from Sprout.config.defaults import default_settings
from Sprout.message.models import Message
from Sprout.orchestration.compiler import ExecutionGraphBuilder
from Sprout.tests.test_r04_e_apply_native import prepare_apply_fixture

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Explicit Windows native CODE integration entry required",
)


@pytest.mark.parametrize("decision", [True, False, "protected"])
async def test_native_cli_code_preparation_and_exact_apply(tmp_path, monkeypatch, decision):
    ctx = await prepare_apply_fixture(tmp_path, monkeypatch, "cli")
    # The shared fixture's frozen.py is intentionally not pytest-discoverable.
    # A normal CLI project also needs its ordinary discovered test suite.
    from Sprout.tests.r04_g1_native_cases import git

    discovered = "tests/test_app.py"
    content = "from src.app import echo\n\ndef test_echo():\n    assert echo(2) == 2\n"
    (ctx["root"] / discovered).write_text(content)
    ctx["files"][discovered] = content
    git(ctx["root"], "add", "--", discovered)
    git(ctx["root"], "commit", "-m", "ordinary CLI test discovery fixture")
    runtime = ctx["runtime"]
    task = ctx["task"]
    session = await runtime.get_session(task.metadata["session_id"])
    workspace = await runtime.get_workspace(task.workspace_id)
    spec = tmp_path / "acceptance.json"
    spec.write_text(json.dumps(task.metadata["host_acceptance_v1"]["spec"]))
    values = iter([",".join(ctx["files"]), "src/app.py", str(spec), "tests/frozen.py",
                   "examples/demo.py", str(ctx["git"]),
                   "cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96",
                   bool(decision)])

    class Prompt:
        async def ask_async(self):
            return next(values)

    monkeypatch.setattr(code_task.questionary, "text", lambda *a, **k: Prompt())
    monkeypatch.setattr(code_task.questionary, "confirm", lambda *a, **k: Prompt())
    runtime._graph_builder = ExecutionGraphBuilder()
    runtime._settings = default_settings()
    spawn = asyncio.create_subprocess_exec
    starts = []

    async def observed_spawn(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        starts.append({"argv": args, "pid": process.pid, "cwd": kwargs.get("cwd"),
                       "pytest_addopts": kwargs.get("env", {}).get("PYTEST_ADDOPTS")})
        (tmp_path / "REAL_PROCESS_STARTS.json").write_text(json.dumps(starts, indent=2))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observed_spawn)
    denied = []
    original_request = runtime._node_tool_request

    async def observe_request(*args):
        try:
            return await original_request(*args)
        except PermissionError as exc:
            denied.append(str(exc))
            raise

    monkeypatch.setattr(runtime, "_node_tool_request", observe_request)
    if decision == "protected":
        from dataclasses import replace

        from Sprout.llm.messages import ToolCall

        response = ctx["model"].responses[1]
        ctx["model"].responses[1] = replace(response, tool_calls=(
            ToolCall("forbidden-test-edit", "sandbox_write_file",
                     {"path": discovered, "content": "raise RuntimeError('changed')\n"}),
            *response.tool_calls))
    try:
        prepared = await code_task.prepare(runtime, workspace.id,
            message=Message("Simplify echo, preserve behavior and run tests"),
            session=session, principal=task.actor)
        if not decision:
            assert prepared is None
            assert not runtime._cli_code_writes
            assert not await runtime.list_change_proposals(task.id)
            return
        assert prepared.id != task.id
        assert prepared.metadata["session_id"] == session.id
        assert prepared.metadata["cli_write_paths"] == ["src/app.py"]
        assert prepared.delegation_scope.allowed_paths
        assert runtime._cli_code_writes[prepared.id][3] == ("src/app.py",)
        async def selected(**request):
            assert request["session"].id == session.id
            assert request["principal"] == task.actor
            return prepared

        result = await runtime._handle_task(
            Message("Simplify echo, preserve behavior and run tests", channel="cli",
                    user_id=task.actor.user_id, session_id=session.id,
                    metadata={"workspace_id": workspace.id}),
            session, principal=task.actor, foreground=True, code_task_provider=selected)
        proposals = await runtime.list_change_proposals(prepared.id)
        (tmp_path / "CLI_PREPARATION_RESULT.json").write_text(json.dumps({
            "task": prepared.id, "result": str(result), "nodes": [
                {"id": n.id, "type": n.type.value, "status": n.status.value,
                 "metadata": n.metadata} for n in
                await runtime.storage.metadata.list_execution_nodes(prepared.id)],
        }, default=str, indent=2))
        if decision == "protected":
            assert "cli_code_write_target_denied" in denied
            sandbox = runtime._frozen_sandboxes[prepared.id][0]
            assert (sandbox.root / discovered).read_text() == content
            assert (ctx["root"] / discovered).read_text() == content
            assert (ctx["root"] / "src/app.py").read_text() == ctx["old"]
            return
        assert len(proposals) == 1
        proposal = proposals[0]
        assert proposal.id in runtime._local_apply_plans
        assert proposal.metadata["git_diff"]["status"] == "available"
        assert len(proposal.metadata["git_diff"]["commands"]) == 5
        confirmations = []

        async def approve(prompt):
            confirmations.append(prompt)
            return "approve"  # Test user only, not a real execution approval.

        monkeypatch.setattr(chat, "_prompt_task_gate_choice", approve)
        monkeypatch.setattr(chat, "current_cli_principal", lambda: task.actor)
        await chat._approve_task_gates(runtime, prepared, "display-only")
        assert confirmations == ["Approve this entire exact Apply Plan?"]
        assert (ctx["root"] / "src/app.py").read_text() == ctx["new"]
        transaction_id = str(uuid5(NAMESPACE_URL, "ordinary-apply:" + proposal.id))
        row = await runtime.storage.metadata.get_apply_transaction(transaction_id)
        (tmp_path / "CLI_APPLY_RESULT.json").write_text(json.dumps(row, default=str, indent=2))
        assert row["phase"] == "COMPLETED"
        context = await runtime._ordinary_conversation_context(
            Message("what changed?", user_id=task.actor.user_id, session_id=session.id),
            await runtime.get_session(session.id), {}, {})
        assert any('"apply_phase": "COMPLETED"' in turn.content
                   for turn in context.memory.working)
    finally:
        await runtime.stop()
        await runtime.storage.close()
