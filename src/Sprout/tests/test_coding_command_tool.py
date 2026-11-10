from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.execution.coding_command_tool import IsolatedCodingCommandTool
from Sprout.execution.models import ProcessResult
from Sprout.orchestration.models import ExecutionNode
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.security.policy import SecurityPolicy
from Sprout.tests.test_isolated_model_tools import fixture
from Sprout.tools.executor import ToolExecutor
from Sprout.tools.registry import ToolRegistry
from Sprout.tools.result import ToolExecutionUnconfirmed


def configured(tmp_path, *, output=None, error=None):
    sandbox, task, _ = fixture(tmp_path)
    handler = SimpleNamespace(execute=AsyncMock(return_value=output, side_effect=error))
    node = ExecutionNode(task_id=task.id)
    return IsolatedCodingCommandTool(handler, task=task, node=node, sandbox=sandbox), handler


async def test_fixed_host_slot_and_import_result(tmp_path):
    tool, handler = configured(tmp_path, output={"process": ProcessResult(("python",),
        allowed=True, exit_code=0, started=True), "imported": True})
    result = await tool.invoke({"command": ["python", "script.py"]})
    assert result.ok and result.data["validation_committed"] is False
    assert handler.execute.call_args.kwargs["command_slot"] == "evaluation-0"
    assert (await tool.invoke({"command": ["different"]})).ok is False
    assert handler.execute.await_count == 1


async def test_model_receives_actual_test_output_without_validation_grant(tmp_path):
    tool, _ = configured(tmp_path, output={"process": ProcessResult(("pytest",),
        exit_code=0, stdout="4 passed in 0.02s", duration_ms=42, started=True), "imported": True})
    result = await tool.invoke({"command": ["pytest"]})
    assert result.ok and "4 passed" in result.to_text()
    assert result.data["duration_ms"] == 42
    assert result.data["validation_committed"] is False


async def test_failure_reports_stderr_and_exit_code_but_no_new_attempt(tmp_path):
    tool, handler = configured(tmp_path, output={"process": ProcessResult(("pytest",),
        exit_code=1, stderr="AssertionError: expected 5 got 0", started=True), "imported": False})
    result = await tool.invoke({"command": ["pytest"]})
    assert not result.ok
    assert "AssertionError" in result.to_text() and "exit_code=1" in result.to_text()
    assert not (await tool.invoke({"command": ["pytest"]})).ok
    assert handler.execute.await_count == 1


@pytest.mark.parametrize("arguments", [{"command": "python script.py"},
    {"command": ["python"], "image": "arbitrary"},
    {"command": ["python"], "command_slot": "evaluation-99"},
    {"command": ["python"], "relative_cwd": "../outside"},
    {"command": ["python"], "relative_cwd": "tests"},
    {"command": ["python"], "relative_cwd": ".git"}])
async def test_model_cannot_choose_authority_or_oracle_cwd(tmp_path, arguments):
    tool, handler = configured(tmp_path)
    assert (await tool.invoke(arguments)).ok is False
    handler.execute.assert_not_called()


async def test_process_success_without_import_is_not_tool_success(tmp_path):
    tool, _ = configured(tmp_path, output={"process": ProcessResult(("python",),
        allowed=True, exit_code=0, started=True), "imported": False})
    assert (await tool.invoke({"command": ["python"]})).ok is False


async def test_unknown_effect_propagates_through_generic_tool_executor(tmp_path):
    tool, handler = configured(tmp_path, error=RuntimeError("unconfirmed import"))
    executor = ToolExecutor(tools=ToolRegistry(),
        policy=SecurityPolicy(always_allow=frozenset({tool.spec.name})),
        policy_engine=Mock(decide=Mock(return_value=PolicyDecision(decision=AccessDecision.ALLOW))))
    scoped = executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
    with pytest.raises(ToolExecutionUnconfirmed):
        await scoped.execute(tool.spec.name, {"command": ["python"]})
    assert handler.execute.await_count == 1


async def test_approval_is_propagated_without_claiming_import(tmp_path):
    tool, _ = configured(tmp_path, output={"process": ProcessResult(("python",),
        allowed=False, approval_id="approval", started=False), "imported": False})
    result = await tool.invoke({"command": ["python"]})
    assert not result.ok and result.approval_id == "approval"


async def test_unknown_tool_effect_keeps_node_uncertain_and_blocks_reentry(tmp_path):
    from dataclasses import replace

    from Sprout.orchestration.execution_guard import NodeExecutionGuard
    from Sprout.orchestration.models import NodeType
    from Sprout.storage.local.sqlite.metadata import open_metadata_store
    from Sprout.task.models import TaskStatus

    root = tmp_path / "sandbox"
    root.mkdir()
    sandbox, task, _ = fixture(root)
    task = replace(task, status=TaskStatus.RUNNING,
                   metadata={**task.metadata, "execution_mode": "code"})
    store = open_metadata_store(str(tmp_path / "metadata.db"))
    handler = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("unknown effect")))
    node = ExecutionNode(task_id=task.id, type=NodeType.AGENT)
    try:
        await store.save_task(task)

        async def execute(running):
            tool = IsolatedCodingCommandTool(handler, task=task, node=running, sandbox=sandbox)
            executor = ToolExecutor(tools=ToolRegistry(),
                policy=SecurityPolicy(always_allow=frozenset({tool.spec.name})),
                policy_engine=Mock(decide=Mock(return_value=PolicyDecision(
                    decision=AccessDecision.ALLOW))))
            return await executor.with_scoped_tools({tool.spec.name: tool}, strict=True).execute(
                tool.spec.name, {"command": ["python"]})

        guard = NodeExecutionGuard(store)
        result = await guard.execute(node, execute)
        assert result["output"]["reason"] == "execution_result_unconfirmed"
        assert (await store.get_execution_node(node.id)).metadata["execution_guard"]["state"] == (
            "uncertain")
        await guard.execute(node, execute, resume_key="new-decision")
        assert handler.execute.await_count == 1
    finally:
        store.close()


async def test_outer_approval_uses_host_context_not_caller_defaults(tmp_path):
    from dataclasses import FrozenInstanceError

    from Sprout.security.approval import ApprovalMode
    from Sprout.tools.context_keys import NodeToolInvocationContext

    tool, handler = configured(tmp_path)
    tool.invocation_context = NodeToolInvocationContext("host-task", "cli", "creator", "workspace")
    with pytest.raises(FrozenInstanceError):
        tool.invocation_context.task_id = "forged"
    approvals = SimpleNamespace(mode_for=Mock(return_value=ApprovalMode.ASK),
        asks_a_human=Mock(return_value=True), is_approved=AsyncMock(return_value=False),
        request=AsyncMock(return_value=SimpleNamespace(id="pending-approval")))
    executor = ToolExecutor(tools=ToolRegistry(), approvals=approvals,
        policy_engine=Mock(decide=Mock(return_value=PolicyDecision(decision=AccessDecision.ALLOW))))
    result = await executor.with_scoped_tools({tool.spec.name: tool}, strict=True).execute(
        tool.spec.name, {"command": ["python"]}, task_id="caller-task", source="interactive",
        requested_by="caller", resource_scope="caller-workspace")
    assert result.approval_id == "pending-approval"
    for call in (approvals.is_approved, approvals.request):
        assert call.call_args.kwargs["task_id"] == "host-task"
        assert call.call_args.kwargs["source"] == "cli"
        assert call.call_args.kwargs["requested_by"] == "creator"
        assert call.call_args.kwargs["resource_scope"] == "workspace"
    handler.execute.assert_not_called()


async def test_bound_command_cannot_be_dispatched_from_global_registry(tmp_path):
    tool, handler = configured(tmp_path)
    registry = ToolRegistry()
    registry.register(tool)
    result = await ToolExecutor(tools=registry).execute(tool.spec.name, {"command": ["python"]})
    assert not result.ok and "strict node-local" in result.error
    handler.execute.assert_not_called()


@pytest.mark.parametrize("first_exit", [1, None, 0])
async def test_foreground_test_repair_allows_one_exact_rerun(tmp_path, first_exit):
    from dataclasses import replace

    sandbox, task, _ = fixture(tmp_path)
    command = ("python", "-m", "unittest", "-v")
    process = ProcessResult(command, exit_code=first_exit, started=True)
    output = {"process": process, "imported": first_exit == 0}
    handler = SimpleNamespace(execute=AsyncMock(return_value=output))
    tool = IsolatedCodingCommandTool(handler, task=task, node=ExecutionNode(task_id=task.id),
        sandbox=replace(sandbox, kind="filesystem_experiment"), filesystem_root_only=True,
        retry_failed_tests=True, require_test_command=True)
    probe = await tool.invoke({"command": ["python", "-c", "print('ready')"]})
    assert not probe.ok and "test slot" in probe.to_text()
    handler.execute.assert_not_awaited()
    await tool.invoke({"command": list(command)})
    assert not (await tool.invoke({"command": ["python", "unrelated.py"]})).ok
    handler.execute.return_value = {
        "process": ProcessResult(command, exit_code=0, started=True), "imported": True,
    }
    assert (await tool.invoke({"command": list(command)})).ok is (first_exit == 1)
    assert not (await tool.invoke({"command": list(command)})).ok
    expected = ["evaluation-0", "evaluation-1"] if first_exit == 1 else ["evaluation-0"]
    assert [call.kwargs["command_slot"] for call in handler.execute.await_args_list] == expected


async def test_incomplete_model_arguments_report_input_error_before_binding(tmp_path):
    tool, handler = configured(tmp_path)
    executor = ToolExecutor(tools=ToolRegistry()).with_scoped_tools(
        {tool.spec.name: tool}, strict=True,
    )
    result = await executor.execute(tool.spec.name, {"_raw": '{"command": ['})
    assert not result.ok and "Incomplete tool arguments" in result.to_text()
    handler.execute.assert_not_awaited()


@pytest.mark.parametrize(("exits", "changed"), [
    ((0,), False), ((1, 0), True), ((1, 0), False), ((1, 1), True), ((1, 1), False),
    ((2,), False), ((2, 0), True),
])
async def test_runtime_runs_frozen_tests_and_repairs_without_model_command(
    tmp_path, exits, changed,
):
    from dataclasses import replace

    from Sprout.message.models import Message
    from Sprout.runtime.nodes import NodeExecutor

    sandbox, task, _ = fixture(tmp_path)
    node = ExecutionNode(task_id=task.id)
    argv = ("python", "-m", "pytest", "-q")
    stdout = ("ERROR collecting: assert price during collection" if exits == (2, 0)
              else "assert price")
    handler = SimpleNamespace(execute=AsyncMock(side_effect=[
        {"process": ProcessResult(argv, exit_code=code, started=True,
                                  stdout=stdout, stderr="test diagnostic"),
         "imported": code == 0}
        for code in exits
    ]))
    tool = IsolatedCodingCommandTool(
        handler, task=task, node=node, sandbox=replace(sandbox, kind="filesystem_experiment"),
        filesystem_root_only=True, retry_failed_tests=True, require_test_command=True,
    )
    executor = Mock()
    executor.with_scoped_tools.return_value.run_call = (
        lambda call, **kwargs: tool.invoke(call.arguments)
    )
    repair = AsyncMock(return_value=SimpleNamespace(metadata={}))
    scheduler = SimpleNamespace(_publish_node_model=repair, _repair_inputs=AsyncMock(
        side_effect=[({"order_total.py": "before"}, {"order_total.py": "current candidate"}),
                     ({"order_total.py": "after" if changed else "before"}, {})]))
    runtime = SimpleNamespace(_node_tool_scopes={}, _record_security=Mock())
    scope = SimpleNamespace(runtime=runtime, session_id="session")
    workspace = SimpleNamespace(id=task.workspace_id)
    plan = {"argv": list(argv), "cwd": ".", "image": "pinned", "repair_attempts": 1,
            "workspace_id": task.workspace_id}
    result = SimpleNamespace(metadata={})
    _, error = await NodeExecutor._run_filesystem_tests(
        scheduler, node, workspace, task, SimpleNamespace(executor=executor),
        Message("implement the fix"), object(), result, tool, plan, scope, "pinned",
    )
    assert (error is None) is (exits[-1] == 0)
    commands = [call.kwargs["command"] for call in handler.execute.await_args_list]
    assert commands == [argv] * len(exits)
    assert all(call.kwargs["task"] is task and call.kwargs["node"] is node
               and call.kwargs["cwd"] == sandbox.root for call in handler.execute.await_args_list)
    assert repair.await_count == (len(exits) - 1)
    if len(exits) == 2:
        feedback = repair.call_args.args[4].content
        expected = (f"exit_code={exits[0]}", "assert price", "test diagnostic")
        assert all(value in feedback for value in (*expected, "current candidate", "SPEC.md"))
        audit = runtime._record_security.call_args.args[1]
        expected_verdict = ("repair_passed" if exits[-1] == 0 else
                            "repair_failed" if changed else "repair_no_effect")
        assert audit["verdict"] == expected_verdict


def test_long_pytest_feedback_retains_middle_assertion_and_summary():
    from Sprout.execution.coding_feedback import command_feedback

    process = ProcessResult(("python", "-m", "pytest"), exit_code=1,
        stdout="padding\n" * 1000 + "E   AssertionError: expected 99.0, got 109.89\n"
        + "padding\n" * 1000 + "FAILED tests/test_order.py::test_discount\n1 failed\n")
    output = command_feedback(process)["output"]
    assert "expected 99.0, got 109.89" in output
    assert "FAILED tests/test_order.py::test_discount" in output
