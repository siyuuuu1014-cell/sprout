import asyncio
import sys
from datetime import UTC, datetime, timedelta

import pytest

from Sprout.security.approval import ApprovalManager, CapabilityTurn
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.tools.context_keys import _cli_process_binding


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["complete", "expire", "revoke"])
async def test_capability_uses_approved_record_and_ends_with_turn(tmp_path, termination):
    manager = ApprovalManager(MemoryOperationalStore())
    turn = CapabilityTurn("user", "session", tmp_path)
    def binding(argument, timeout):
        return _cli_process_binding(
            (sys.executable, argument), tmp_path, {}, timeout, turn.id,
            turn.session_id, "cli", turn.user_id, str(tmp_path), "", "local_directory",
        )
    record = await manager.request(
        "cli_tool_run", {"command": sys.executable, "args": ["-V"]},
        task_id=turn.id, requested_by=turn.user_id, session_id=turn.session_id,
        resource_scope=str(tmp_path), source="cli",
    )
    turn.offer(record.id, binding("-V", 10))
    await manager.decide_task_capability(turn, record.id, decided_by="user")
    assert await manager.task_capability(turn, binding("--version", 20))
    assert not await manager.task_capability(
        CapabilityTurn("user", "session", tmp_path), binding("--version", 20),
    )
    if termination == "expire":
        expired = await manager.store.get_approval(record.id)
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await manager.store.save_approval(expired)
    elif termination == "revoke":
        await manager.revoke_task_capability(turn, record.id)
    else:
        turn.active = False
    assert not await manager.task_capability(turn, binding("--version", 20))


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["approve_task", "deny", "cancel"])
async def test_cli_turn_runs_five_real_commands(tmp_path, monkeypatch, choice):
    from Sprout.cli.commands import chat
    from Sprout.gateway.identity import Principal
    from Sprout.llm.messages import LLMResponse, Role, ToolCall
    from Sprout.message.models import Message
    from Sprout.tests.conftest import ScriptedModel, build_runtime

    monkeypatch.chdir(tmp_path)
    principal = Principal("a2-owner", source="cli", authenticated=True)

    def call(index):
        return ToolCall(id=f"call-{index}", name="cli_tool_run", arguments={
            "command": sys.executable, "args": ["-c", f"print('a2-result-{index}')"],
            "timeout": 10 + index,
        })

    class Model(ScriptedModel):
        async def chat(self, messages, *, tools=()):
            outputs.extend(str(message.content) for message in messages if message.role is Role.TOOL)
            return await super().chat(messages, tools=tools)

    outputs = []
    launches = []
    spawn = asyncio.create_subprocess_exec

    async def record_spawn(*argv, **kwargs):
        if len(argv) == 3 and argv[1] == "-c" and "a2-result-" in argv[2]:
            launches.append(argv[2])
        return await spawn(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_spawn)
    model = Model([
        LLMResponse(content=None, tool_calls=(call(0),), finish_reason="tool_calls"),
        LLMResponse(content=None, tool_calls=tuple(map(call, range(1, 5))),
                    finish_reason="tool_calls"),
        LLMResponse(content="finished", finish_reason="stop"),
        LLMResponse(content=None, tool_calls=(call(0),), finish_reason="tool_calls"),
    ])
    runtime, executor = build_runtime(model=model)
    executor.approvals = runtime.approvals
    prompts = []

    async def prompt(record, *, task_executable=None):
        assert task_executable
        prompts.append(record.id)
        return choice

    monkeypatch.setattr(chat, "_prompt_approval_decision", prompt)
    monkeypatch.setattr(chat, "current_cli_principal", lambda: principal)
    await runtime.start()
    try:
        first = await runtime.handle(
            Message("Please inspect these five command outputs", channel="cli"),
            principal=principal,
        )
        assert first.metadata["approval_required"]
        if choice == "cancel":
            approval_id = first.metadata["approval_ids"][0]
            await runtime.approve_cli_task(first.session_id, approval_id, principal=principal)
            await runtime.cancel_cli_task(first.session_id, principal=principal)
            with pytest.raises(LookupError, match="active CLI turn"):
                await runtime.resume_pending_task(first.session_id, principal=principal)
            assert not launches
            return
        await chat._handle_cli_approval_prompt(
            runtime, first.session_id, first.metadata, principal.user_id,
        )
        assert len(prompts) == 1
        if choice == "approve_task":
            for i in range(5):
                assert any(f"a2-result-{i}" in output for output in outputs), outputs
            assert launches == [f"print('a2-result-{i}')" for i in range(5)]
            assert not await runtime.approvals.pending()
            assert first.session_id not in runtime._capability_turns
            next_turn = await runtime.handle(
                Message("Please inspect a fresh command output", channel="cli",
                        session_id=first.session_id),
                principal=principal,
            )
            assert next_turn.metadata["approval_required"]
        else:
            assert not outputs and not launches
            assert not await runtime.approvals.pending()
        print(f"{choice}: prompts={len(prompts)} executions={len(launches)} "
              f"commands={launches} outputs={list(dict.fromkeys(outputs))}")
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve_task", "deny"])
async def test_cli_approves_two_programs_in_one_prompt(tmp_path, monkeypatch, decision):
    import shutil

    from Sprout.cli.commands import chat
    from Sprout.gateway.identity import Principal
    from Sprout.llm.messages import LLMResponse, Role, ToolCall
    from Sprout.message.models import Message
    from Sprout.storage.bundle import StorageBundle
    from Sprout.storage.local.sqlite.driver import SqliteDatabase
    from Sprout.storage.local.sqlite.metadata import SqliteMetadataStore
    from Sprout.tests.conftest import ScriptedModel, build_runtime

    git = shutil.which("git")
    if not git:
        pytest.skip("Git executable unavailable")
    monkeypatch.chdir(tmp_path)
    principal = Principal("a2-owner", source="cli", authenticated=True)
    commands = [
        (sys.executable, ["-c", "print('multi-0')"]),
        (git, ["--version"]),
        (sys.executable, ["-c", "print('multi-2')"]),
        (git, ["--exec-path"]),
        (sys.executable, ["-c", "print('multi-4')"]),
    ]

    def response(indexes):
        return LLMResponse(content=None, tool_calls=tuple(
            ToolCall(id=f"multi-{i}", name="cli_tool_run", arguments={
                "command": commands[i][0], "args": commands[i][1], "timeout": 10 + i,
            })
            for i in indexes
        ), finish_reason="tool_calls")

    outputs = {}
    launches = []
    spawn = asyncio.create_subprocess_exec

    async def record_spawn(*argv, **kwargs):
        process = await spawn(*argv, **kwargs)
        launches.append(argv)
        return process

    class Model(ScriptedModel):
        async def chat(self, messages, *, tools=()):
            outputs.update((message.tool_call_id, message.content) for message in messages
                           if message.role is Role.TOOL)
            return await super().chat(messages, tools=tools)

    model = Model([response(range(2)), response(range(2, 5)),
                   LLMResponse(content="finished", finish_reason="stop")])
    database = SqliteDatabase(tmp_path / "metadata.db")
    storage = StorageBundle.in_memory()
    storage.metadata = SqliteMetadataStore(database)
    runtime, executor = build_runtime(model=model, storage=storage)
    executor.approvals = runtime.approvals
    prompts = []

    async def prompt(record, *, task_executable=None):
        prompts.append(task_executable)
        return decision

    monkeypatch.setattr(chat, "_prompt_approval_decision", prompt)
    monkeypatch.setattr(chat, "current_cli_principal", lambda: principal)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_spawn)
    await runtime.start()
    try:
        workspace = await runtime.open_workspace(tmp_path)
        first = await runtime.handle(Message("Inspect these local outputs", channel="cli"),
                                     principal=principal)
        assert len(first.metadata["approval_ids"]) == 2
        await chat._handle_cli_approval_prompt(
            runtime, first.session_id, first.metadata, principal.user_id,
        )
        assert len(prompts) == 1 and sys.executable in prompts[0] and git in prompts[0]
        approvals = await runtime.approvals.store.list_approvals()
        assert len(approvals) == 2
        assert all(record.resource_scope == f"{workspace.id}:{tmp_path.resolve()}"
                   for record in approvals)
        assert not await runtime.approvals.pending()
        if decision == "deny":
            assert all(record.status.value == "rejected" for record in approvals)
            assert not launches and not outputs
            print("batch deny: prompts=1 records=2 spawns=0")
            return
        assert launches == [(program, *args) for program, args in commands]
        assert len(outputs) == 5
        assert "git version" in outputs["multi-1"]
        assert outputs["multi-3"].strip()
        for i in (0, 2, 4):
            assert outputs[f"multi-{i}"].strip() == f"multi-{i}"
        print(f"prompts=1 records=2 successful_tools={len(outputs)} spawns={len(launches)}")
        print(outputs)
    finally:
        await runtime.stop()
        database.close()

@pytest.mark.asyncio
async def test_durable_coding_capability_across_nodes_and_store_reopen(tmp_path, monkeypatch):
    from dataclasses import replace

    from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
    from Sprout.security.approval import ApprovalStatus
    from Sprout.storage.local.sqlite.driver import SqliteDatabase
    from Sprout.storage.local.sqlite.operational import SqliteOperationalStore
    from Sprout.task.models import TaskStatus
    from Sprout.tests.test_coding_commands import prepared

    metadata, task, first, _, commands, execute = await prepared(tmp_path, monkeypatch)
    task = replace(task, status=TaskStatus.RUNNING)
    await metadata.save_task(task)
    database = SqliteDatabase(tmp_path / "a2-approvals.db")
    try:
        manager = ApprovalManager(SqliteOperationalStore(database))
        pending = await commands.approval(manager, task=task, node=first)
        assert pending.status is ApprovalStatus.PENDING
        assert (await commands.approval(manager, task=task, node=first)).id == pending.id
        execute.assert_not_called()
        await manager.decide(pending.id, True, decided_by=task.actor.user_id,
                             principal=task.actor)
        assert await metadata.finish_execution_node(
            replace(first, status=NodeStatus.COMPLETED), "agent-owner", "completed",
        )
        second = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
        await metadata.save_execution_node(second)
        assert await metadata.claim_execution_node(second, "second-owner", "")
        second = await metadata.get_execution_node(second.id)

        database.close()
        database = SqliteDatabase(tmp_path / "a2-approvals.db")
        reopened = ApprovalManager(SqliteOperationalStore(database))
        assert (await commands.approval(reopened, task=task, node=second)).id == pending.id
        assert len(await reopened.store.list_approvals()) == 1
        other_task = replace(task, id="other-coding-task")
        assert (await reopened.coding_task_capability(other_task, commands._binding)).id != pending.id
        expired = await reopened.store.get_approval(pending.id)
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await reopened.store.save_approval(expired)
        assert (await commands.approval(reopened, task=task, node=second)).id != pending.id
        expired.expires_at = datetime.now(UTC) + timedelta(minutes=1)
        await reopened.store.save_approval(expired)
        with pytest.raises(ValueError, match="ownership"):
            await commands.approval(reopened, task=task, node=first)
        assert await reopened.revoke_coding_task_capability(pending.id, principal=task.actor)
        replacement = await commands.approval(reopened, task=task, node=second)
        assert replacement.id != pending.id and replacement.status is ApprovalStatus.PENDING
        await reopened.decide(replacement.id, False, decided_by=task.actor.user_id,
                              principal=task.actor)
        assert (await commands.approval(reopened, task=task, node=second)).status is (
            ApprovalStatus.REJECTED
        )
        for status in (TaskStatus.CANCELLED, TaskStatus.COMPLETED):
            await metadata.save_task(replace(task, status=status))
            with pytest.raises(PermissionError, match="live coding task"):
                await commands.approval(reopened, task=task, node=second)
    finally:
        database.close()
        metadata.close()

@pytest.mark.parametrize("case", ["valid", "changed", "task", "owner", "node", "unguarded"])
def test_coding_resume_context_requires_exact_guarded_calls(monkeypatch, case):
    from copy import deepcopy
    from types import SimpleNamespace

    from Sprout.context.builder import ContextBuilder
    from Sprout.storage.bundle import StorageBundle

    stored = [{"id": "call", "name": "isolated_coding_command",
               "arguments": {"command": ["python", "--version"]}}]
    scope = SimpleNamespace(
        node=SimpleNamespace(id="node", metadata={
            "execution_guard": {"state": "active", "owner": "owner", "resume_key": "decision"},
            "output": {"waiting": True, "pending_approvals": ["approval"],
                       "pending_tool_calls": stored},
        }),
        task=SimpleNamespace(id="task", actor=SimpleNamespace(user_id="user")),
        guard_owner="owner", captured_paths=(),
        runtime=SimpleNamespace(_node_context_resources=lambda *_: True),
    )
    monkeypatch.setattr("Sprout.tools.context_keys._current_node_publication", lambda: scope)
    calls = deepcopy(stored)
    task_id, node_id = "task", "node"
    if case == "changed":
        calls[0]["arguments"]["command"].append("modified")
    elif case == "task":
        task_id = "other"
    elif case == "owner":
        scope.guard_owner = "other"
    elif case == "node":
        node_id = "other"
    elif case == "unguarded":
        scope.node.metadata["execution_guard"]["resume_key"] = ""
    builder = ContextBuilder(StorageBundle.in_memory())

    def build():
        return builder.build_node_context(
            user_id="user", node_id=node_id,
            metadata={"task_id": task_id, "resume_tool_calls": calls},
        )

    if case == "valid":
        context = build()
        calls[0]["arguments"]["command"].append("mutated after admission")
        assert context.metadata["resume_tool_calls"] == stored
    else:
        with pytest.raises(PermissionError, match="node_artifact_provenance_unavailable"):
            build()

@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["allowed", "outside_scope", "outside_sandbox", "workspace"])
async def test_coding_import_attribution_rejects_paths_outside_task_scope(
    tmp_path, monkeypatch, case,
):
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.execution.coding_command_tool import IsolatedCodingCommandTool
    from Sprout.runtime.runtime import Runtime
    from Sprout.security.access import ActionRequest, ActionType
    from Sprout.task.models import DelegationScope, Task
    from Sprout.workspace.models import ResourceRef

    sandbox = SimpleNamespace(root=tmp_path / "sandbox")
    tool = object.__new__(IsolatedCodingCommandTool)
    tool._sandbox = sandbox
    task = Task(workspace_id="workspace", delegation_scope=DelegationScope(
        allowed_paths=("app.py",),
    ))
    scope = SimpleNamespace(task=task)
    monkeypatch.setattr("Sprout.tools.context_keys._current_node_tool_call",
                        lambda: (tool, None, {}, scope))
    subject = ActionRequest(task_id=task.id, actor=task.actor, action=ActionType.TOOL_INVOKE)
    stored = replace(task, workspace_id="other") if case == "workspace" else task
    runtime = SimpleNamespace(
        _node_tool_request=AsyncMock(return_value=subject),
        _metadata_or_raise=lambda: SimpleNamespace(get_task=AsyncMock(return_value=stored)),
        _check_cli_write_target=lambda *_: None,
    )
    target = {
        "allowed": sandbox.root / "app.py",
        "workspace": sandbox.root / "app.py",
        "outside_scope": sandbox.root / "other.py",
        "outside_sandbox": tmp_path / "app.py",
    }[case]
    request = ActionRequest(task_id=task.id, action=ActionType.FILE_WRITE,
                            resource=ResourceRef("", str(target)))
    if case == "allowed":
        result = await Runtime._node_file_request(runtime, request, sandbox)
        assert result.resource.workspace_id == task.workspace_id
    else:
        with pytest.raises(PermissionError, match="node_file_"):
            await Runtime._node_file_request(runtime, request, sandbox)

@pytest.mark.asyncio
async def test_cli_git_task_grant_cannot_start_git_writes(tmp_path, monkeypatch):
    import shutil
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.security.approval import _CAPABILITY_TURN
    from Sprout.security.layered_policy import DEFAULT_ORGANIZATION_DENY, LayeredPolicyEngine, PolicyLayer
    from Sprout.tests.conftest import build_runtime

    git = shutil.which("git")
    if git is None:
        pytest.skip("Git executable unavailable")
    monkeypatch.chdir(tmp_path)
    runtime, executor = build_runtime()
    executor.approvals = runtime.approvals
    runtime._process_broker._policy = LayeredPolicyEngine(
        organization=PolicyLayer.from_command_globs("organization", DEFAULT_ORGANIZATION_DENY),
    )
    spawn = AsyncMock(return_value=SimpleNamespace(
        communicate=AsyncMock(return_value=(b"git fixture", b"")), returncode=0,
    ))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    turn = CapabilityTurn("owner", "session", tmp_path.resolve())
    token = _CAPABILITY_TURN.set(turn)
    async def invoke(*argv):
        return await executor.execute("cli_tool_run", {"command": "git", "args": list(argv)},
                                      requested_by=turn.user_id, session_id=turn.session_id)
    try:
        offer = await invoke("--version")
        await runtime.approvals.decide_task_capability(turn, offer.approval_id, decided_by="owner")
        assert (await invoke("--version")).ok
        assert (await invoke("--exec-path")).ok
        for argv in (("push", "origin", "test-branch"), ("commit", "-m", "test")):
            result = await invoke(*argv)
            assert result.approval_id and result.approval_id != offer.approval_id
        for force in (
            ("push", "--force", "origin", "test-branch"),
            ("-C", str(tmp_path), "push", "--force", "origin", "test-branch"),
            ("push", "origin", "test-branch", "--force"),
            ("push", "origin", "test-branch", "-f"),
        ):
            withheld = await invoke(*force)
            assert not withheld.ok and withheld.approval_id
            await runtime.approvals.decide(withheld.approval_id, True, decided_by="owner")
            denied = await invoke(*force)
            assert not denied.ok and not denied.approval_id
            assert "deny" in denied.error.lower() or "force" in denied.error
        assert not set(turn.offers) - {offer.approval_id}
        assert [call.args[1:] for call in spawn.await_args_list] == [
            ("--version",), ("--exec-path",),
        ]
    finally:
        _CAPABILITY_TURN.reset(token)
        await runtime.stop()
