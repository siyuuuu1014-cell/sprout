"""Native temporary Runtime/SQLite approval wait, never historical approvals."""

import asyncio
import json
import os
import tempfile
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.tests.test_r04_e_apply_native import FixturePolicy, prepare_apply_fixture

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Explicit Windows native entry required",
)
COMMAND = ("python", "-m", "compileall", "-q", "src/app.py")


class AskForCommand(FixturePolicy):
    commands = (COMMAND,)

    def decide(self, request):
        decision = super().decide(request)
        if (request.action is ActionType.PROCESS_RUN
                and tuple(request.arguments.get("command", ())) in self.commands
                and decision.decision is not AccessDecision.DENY):
            return PolicyDecision(AccessDecision.REQUIRE_APPROVAL, "native fixture exact command")
        return decision


class AskForLocalCase(AskForCommand):
    def decide(self, request):
        decision = FixturePolicy.decide(self, request)
        if request.action is not ActionType.PROCESS_RUN:
            return decision
        from Sprout.tools.context_keys import _current_project_node

        scope = _current_project_node()
        command = request.arguments.get("command", ())
        if request.action is ActionType.PROCESS_RUN:
            self.observations.append({"node": scope.node.type.value if scope else None,
                                      "argv": list(command)})
        if (request.action is ActionType.PROCESS_RUN and scope is not None
                and scope.node.type.value == "evaluation" and command
                and '"case_id": "normal"' in command[-1]
                and decision.decision is not AccessDecision.DENY):
            return PolicyDecision(AccessDecision.REQUIRE_APPROVAL, "exact frozen local case")
        return decision


@pytest.mark.parametrize("mode", [
    "approve", "reject", "expired", "wrong_id", "plan_changed",
    "owner_changed", "session_changed", "task_changed", "sandbox_changed",
    "approval_changed", "input_replacement", "unknown", "budget_approve", "budget_reject",
    "budget_sequence", "budget_local",
])
async def test_exact_command_waits_for_real_approval(tmp_path, monkeypatch, mode):
    wait_beyond_budget = mode.startswith("budget_")
    mode = mode.removeprefix("budget_")
    local = mode == "local"
    if local:
        mode = "approve"
    sequence = mode == "sequence"
    commands = (COMMAND, ("python", "-m", "compileall", "-q", "-f", "src/app.py")) if (
        sequence) else (COMMAND,)
    if sequence:
        mode = "approve"
    ctx = await prepare_apply_fixture(tmp_path, monkeypatch, "cli")
    runtime, store = ctx["runtime"], ctx["storage"].metadata
    task = replace(ctx["task"], delegation_scope=replace(
        ctx["task"].delegation_scope, allowed_paths=tuple(ctx["files"])))
    await store.save_task(task)
    graph = replace(ctx["graph"], nodes=tuple(
        replace(n, metadata={**n.metadata, "test_commands": [" ".join(c) for c in commands]},
                budget=replace(n.budget, timeout_seconds=10) if wait_beyond_budget else n.budget)
        if n.type.value == "evaluation" else n for n in ctx["graph"].nodes))
    monkeypatch.setattr(runtime._graph_builder, "build", lambda *a, **kw: graph)
    factory = runtime._executor

    def executor(metadata):
        instance = factory(metadata)
        instance._static_check_commands = AsyncMock(return_value=[])
        return instance

    monkeypatch.setattr(runtime, "_executor", executor)
    policy = AskForLocalCase() if local else AskForCommand()
    policy.commands = commands
    policy.observations = []
    runtime._policy_engine = runtime._process_broker._policy = runtime._file_broker._policy = policy
    await runtime.configure_local_git_review(
        task.id, task.actor, executable=ctx["git"],
        executable_sha256="cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96",
        export_parent=tempfile.mkdtemp(prefix="r04-approval-wait-"))
    starts, returned = [], asyncio.Event()
    spawn = asyncio.create_subprocess_exec

    async def observed_spawn(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        from Sprout.tools.context_keys import _current_project_node

        scope = _current_project_node()
        if (not local and args in commands) or (local and scope is not None
                and scope.node.type.value == "evaluation"
                and '"case_id": "normal"' in args[-1]):
            starts.append({"argv": list(args), "cwd": str(kwargs.get("cwd")), "pid": process.pid})
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observed_spawn)
    broker = runtime._process_broker
    observed_method = "run_local_acceptance" if local else "run_evaluation_command"
    original = getattr(broker, observed_method)

    async def observed_command(*args, **kwargs):
        try:
            return await original(*args, **kwargs)
        finally:
            returned.set()

    monkeypatch.setattr(broker, observed_method, observed_command)
    frozen = broker._run_frozen_contract

    async def fault_id(*args, **kwargs):
        result = await frozen(*args, **kwargs)
        if mode == "wrong_id" and result.approval_id and not result.started:
            return replace(result, approval_id="nonexistent-test-approval")
        if mode == "approval_changed" and result.approval_id and not result.started:
            other = await runtime._approvals.request(
                "process_run", {"action": "different-execution"}, task_id=task.id,
                session_id=task.metadata["session_id"], requested_by=task.actor.user_id,
                source=task.source, resource_scope=task.workspace_id)
            return replace(result, approval_id=other.id)
        if mode == "unknown" and result.started and result.command == COMMAND:
            return replace(result, exit_code=None, error="test-injected missing exit confirmation")
        return result

    monkeypatch.setattr(broker, "_run_frozen_contract", fault_id)
    running = asyncio.create_task(runtime._execute_task(task, artifact_snapshot=None))
    evidence = {"mode": mode, "task": task.id}
    try:
        async with asyncio.timeout(30):
            while True:
                approvals = await runtime._approvals.store.list_approvals()
                record = next((a for a in approvals if a.task_id == task.id and (
                    local or '"argv"' in a.action_summary)), None)
                if record is not None:
                    break
                if running.done():
                    pytest.fail("Runtime ended before pending approval")
                await asyncio.sleep(.05)
        evidence["pending"] = asdict(record)
        returned.clear()
        await asyncio.sleep(10.2 if wait_beyond_budget else .15)
        assert not starts
        if wait_beyond_budget:
            assert not running.done(), "human approval wait consumed the node execution budget"
        if mode not in {"wrong_id", "approval_changed"}:
            assert not returned.is_set(), "command returned before its approval decision"
        if mode == "expired":
            record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await runtime._approvals.store.save_approval(record)
        elif mode == "plan_changed":
            key = next(k for k in runtime._local_receipt_plans if k[0] == task.id)
            runtime._local_receipt_plans[key] = []
            await runtime._approvals.decide(record.id, True, decided_by="native-test-human")
        elif mode in {"session_changed", "task_changed"}:
            changed = await store.get_task(task.id)
            changed = (replace(changed, metadata={**changed.metadata, "session_id": "changed"})
                       if mode == "session_changed" else
                       replace(changed, actor=replace(changed.actor, user_id="changed-owner")))
            await store.save_task(changed)
            await runtime._approvals.decide(record.id, True, decided_by="native-test-human")
        elif mode == "owner_changed":
            nodes = await store.list_execution_nodes(task.id)
            node = next(n for n in nodes if n.type.value == "evaluation")
            with pytest.raises(ValueError, match="owner-checked settlement"):
                await store.save_execution_node(replace(node, metadata={**node.metadata,
                    "execution_guard": {**node.metadata["execution_guard"], "owner": "changed"}}))
            persisted = await store.get_execution_node(node.id)
            assert persisted.metadata["execution_guard"]["owner"] == (
                node.metadata["execution_guard"]["owner"])
            await runtime._approvals.decide(record.id, False, decided_by="native-test-human")
        elif mode == "sandbox_changed":
            runtime._frozen_sandboxes.pop(task.id)
            await runtime._approvals.decide(record.id, True, decided_by="native-test-human")
        elif mode == "approval_changed":
            pass  # The broker received a real, separately bound approval ID.
        elif mode == "input_replacement":
            source = runtime._frozen_sandboxes[task.id][0].root / "src/app.py"
            before = source.read_bytes()
            with pytest.raises(PermissionError):
                source.write_bytes(b"changed during wait")
            assert source.read_bytes() == before
            await runtime._approvals.decide(record.id, False, decided_by="native-test-human")
        elif mode != "wrong_id":
            await runtime._approvals.decide(
                record.id, mode in {"approve", "unknown"}, decided_by="native-test-human")
        if sequence:
            async with asyncio.timeout(20):
                while True:
                    second = next((a for a in await runtime._approvals.store.list_approvals()
                                   if a.task_id == task.id and a.id != record.id
                                   and a.status.value == "pending"), None)
                    if second is not None:
                        break
                    assert not running.done()
                    await asyncio.sleep(.05)
            await asyncio.sleep(10.2)
            assert not running.done() and len(starts) == 1
            await runtime._approvals.decide(second.id, True, decided_by="native-test-human")
            evidence["second_approval"] = asdict(second)
        async with asyncio.timeout(40):
            await running
        evidence["nodes"] = [
            {"type": n.type.value, "status": n.status.value, "metadata": n.metadata}
            for n in await store.list_execution_nodes(task.id)]
        settled = await runtime._approvals.store.get_approval(record.id)
        evidence["approval"] = asdict(settled)
        if mode in {"approve", "unknown"}:
            assert len(starts) == len(commands)
            if local:
                # Existing frozen Local Acceptance is Task-bound reusable approval;
                # ordinary evaluation commands retain their single-use contract.
                assert not settled.single_use
                assert settled.status.value == "approved" and settled.used_at is None
            else:
                assert settled.single_use
                assert settled.status.value == "consumed" and settled.used_at is not None
            evaluation = next(n for n in evidence["nodes"] if n["type"] == "evaluation")
            checks = evaluation["metadata"]["output"]["results"]
            check = next(c for c in checks if c["kind"] == "acceptance:normal") if (
                local) else checks[0]
            assert check["started"]
            assert check["exit_code"] == (0 if mode == "approve" else None)
        else:
            assert not starts
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        evidence["starts"] = starts
        evidence["policy"] = policy.observations
        (tmp_path / "WAIT_EVIDENCE.json").write_text(json.dumps(evidence, default=str, indent=2))
        await runtime.stop()
        store.close()
