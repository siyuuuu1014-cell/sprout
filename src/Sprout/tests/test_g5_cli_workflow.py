import pytest

from Sprout.cli.filesystem_preparation import plan_files
from Sprout.cli.i18n import language_request
from Sprout.intents import detect_task_hint
from Sprout.runtime.isolated_test_run import execution_request
from Sprout.tests.test_r04_b4_cli_auto import automatic as automatic
from Sprout.tests.test_r04_b4_cli_auto import captured as captured
from Sprout.tests.test_r04_b4_cli_auto import cli as cli
from Sprout.tests.test_r04_b4_cli_auto import rig as rig


def test_bound_project_function_change_has_coding_intent():
    assert detect_task_hint("请修改 app.py 中 subtract 函数的文档字符串。")
    assert detect_task_hint("请在当前项目中新增 subtract(a, b) 函数，实现两个数字相减。")
    assert not detect_task_hint("请解释如何修改 app.py。")


def test_single_source_function_addition_keeps_explicit_test_file():
    plan = plan_files(
        ("app.py", "test_app.py"),
        "请在当前项目中新增 subtract(a, b) 函数，实现两个数字相减。更新 test_app.py。",
    )
    assert plan["reads"] == ("app.py", "test_app.py")
    assert set(plan["writes"]) == {"app.py", "test_app.py"}


def test_explicit_source_wins_and_implicit_target_must_be_unambiguous():
    paths = ("app.py", "utils.py")
    assert plan_files(paths, "在 app.py 中新增 subtract(a, b) 函数。")["writes"] == ("app.py",)
    with pytest.raises(PermissionError, match="filesystem_planning_ambiguous_source_target"):
        plan_files(paths, "新增 subtract(a, b) 函数。")


def test_only_tests_and_coding_plus_tests_are_distinct():
    assert execution_request("只运行当前项目的全部测试，不修改任何文件。")
    assert not execution_request("修改 app.py 中的函数，并运行全部测试。")
    assert not execution_request("不要运行当前项目的测试。")


def test_language_qualifier_does_not_swallow_long_answer_request():
    assert language_request("请用至少3000个中文汉字，详细介绍一个模块化软件工程架构。") is None


async def test_legacy_native_probe_is_denied_but_not_advertised():
    from Sprout.tools.project_analyze_tool import ProjectAnalyzeTool

    tool = ProjectAnalyzeTool()
    assert "include_system_tools" not in tool.spec.input_schema["properties"]
    result = await tool.invoke({"path": ".", "include_system_tools": True})
    assert not result.ok
    assert result.error == "native_project_probe_unavailable"


def test_project_counts_use_indexed_methods_and_group_classes_by_file():
    from types import SimpleNamespace

    from Sprout.workspace.graph import WorkspaceNode
    from Sprout.workspace.query import WorkspaceQuery

    def node(path, kind, qualified_name=""):
        return WorkspaceNode(resource=SimpleNamespace(path=path), kind=kind,
                             name=f"{path}:{qualified_name}", qualified_name=qualified_name,
                             granularity="symbol" if qualified_name else "file")

    graph = SimpleNamespace(nodes=(
        node("app.py", "function", "subtract"),
        node("test_app.py", "method", "Tests.test_subtract"),
        node("test_app.py", "method", "Tests.test_zero"),
        node("test_app.py", "method", "Tests.helper"),
        node("test_other.py", "method", "Tests.test_add"),
        node("test_other.py", "test", "test_standalone"),
        node("test_other.py", "test"),  # file-level graph node, not a callable
    ))
    query = WorkspaceQuery(SimpleNamespace(graph=graph), captured=True)
    assert query.indexed_counts() == {
        "files": 3, "functions": 2, "test_classes": 2, "test_methods": 3, "test_functions": 1,
    }


@pytest.mark.parametrize("symbol", ["main", "missing_function"])
async def test_planner_validates_function_against_authorized_discovery(automatic, symbol):
    from Sprout.cli.filesystem_preparation import discover_plan
    from Sprout.message.models import Message

    cli = automatic
    cli.state.answers = [True]
    cli.state.expected_prompt_classifications = 0
    runtime, session, workspace = cli.rig.runtime, cli.rig.session, cli.rig.workspace
    principal = runtime._sessions.frozen_principal(session)
    await runtime._remember_session_workspace(session.id, workspace.id)
    message = Message(f"请修改 src/main.py 中 {symbol} 函数的文档字符串。",
                      channel="cli", session_id=session.id, user_id=principal.user_id)
    if symbol == "main":
        plan = await discover_plan(runtime, workspace, message, session, principal)
        assert plan["writes"] == ("src/main.py",)
    else:
        with pytest.raises(PermissionError, match="filesystem_planning_function_not_indexed"):
            await discover_plan(runtime, workspace, message, session, principal)


@pytest.mark.parametrize("case", ["persisted", "mismatch", "missing"])
async def test_code_preparation_uses_owned_session_workspace(tmp_path, monkeypatch, case):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.cli.code_task import prepare
    from Sprout.gateway.identity import Principal
    from Sprout.message.models import Message
    from Sprout.workspace.models import Workspace

    principal = Principal("owner", authenticated=True, source="cli")
    owned = SimpleNamespace(id="session")
    root = tmp_path / "gone" if case == "missing" else tmp_path
    workspace = Workspace("bound", root)
    runtime = SimpleNamespace(
        _sessions=SimpleNamespace(require_owner=AsyncMock(return_value=owned),
                                  frozen_principal=lambda _: principal),
        session_workspace_id=lambda _: workspace.id,
        get_workspace=AsyncMock(return_value=workspace),
    )
    prepare_fs = AsyncMock(return_value="isolated-test-task")
    monkeypatch.setattr("Sprout.cli.filesystem_task.prepare_filesystem", prepare_fs)
    message = Message("只运行当前项目的全部测试，不修改任何文件。", channel="cli")
    if case == "persisted":
        assert await prepare(runtime, None, message=message, session=owned,
                             principal=principal) == "isolated-test-task"
        runtime.get_workspace.assert_awaited_once_with("bound")
        prepare_fs.assert_awaited_once_with(
            runtime, workspace, message, owned, principal, test_only=True,
        )
    else:
        error = "cli_code_subject_unavailable" if case == "mismatch" else (
            "cli_code_workspace_unavailable"
        )
        with pytest.raises(PermissionError, match=error):
            await prepare(runtime, "other" if case == "mismatch" else None,
                          message=message, session=owned, principal=principal)
        prepare_fs.assert_not_awaited()
        if case == "mismatch":
            runtime.get_workspace.assert_not_awaited()


def test_analysis_and_test_execution_has_an_isolated_route():
    assert execution_request("分析项目并运行全部测试。")
    assert not execution_request("分析项目，不做任何修改。")
    assert not execution_request("分析项目并修改 app.py 后运行测试。")


def test_conditional_test_addition_is_a_coding_request():
    assert detect_task_hint("检查现有测试是否充分，不足时补充。")


def test_coverage_check_is_explicit_read_only_analysis():
    from Sprout.runtime.analysis_consent import explicit_request

    assert explicit_request("检查 greet 和 add 的测试覆盖。")
    assert not detect_task_hint("检查 greet 和 add 的测试覆盖。")


SQLITE_PROJECT_REQUESTS = {
    17: (
        '只运行当前项目已有的全部单元测试。\n'
        '\n'
        '要求：\n'
        '1. 自动发现测试文件。\n'
        '2. 自动识别测试框架。\n'
        '3. 自动检查 Docker 环境。\n'
        '4. 使用 Docker 隔离执行。\n'
        '5. 返回实际执行的命令。\n'
        '6. 返回通过、失败、跳过数量。\n'
        '7. 不修改任何项目文件。'
    ),
    19: (
        '请在当前项目的 app.py 中新增一个 subtract(a, b) 函数，实现两个数字相减。\n'
        '\n'
        '要求：\n'
        '1. 添加类型标注。\n'
        '2. 补充清晰的函数文档。\n'
        '3. 在 test_app.py 中增加对应单元测试。\n'
        '4. 覆盖正数、负数、零和混合符号。\n'
        '5. 运行项目全部测试。\n'
        '6. 确保原有 greet 和 add 函数行为不变。\n'
        '7. 测试全部通过后，展示实际代码 diff。\n'
        '8. 完成独立 Verification 和 Review。\n'
        '9. 最后将修改写回当前项目。'
    ),
    21: (
        '请修改 app.py 中 subtract 函数的文档字符串，使其明确说明参数、返回值和使用示例。\n'
        '\n'
        '不要修改其他函数。\n'
        '运行现有全部测试，确认没有回归。'
    ),
    23: (
        '检查当前项目的 greet 和 add 函数，判断现有单元测试是否已经覆盖主要行为。\n'
        '\n'
        '如果已经覆盖，不要修改任何文件，直接给出分析结果。'
    ),
}


def test_complete_sqlite_requests_keep_the_primary_operation():
    assert execution_request(SQLITE_PROJECT_REQUESTS[17])
    assert detect_task_hint(SQLITE_PROJECT_REQUESTS[19])
    assert detect_task_hint(SQLITE_PROJECT_REQUESTS[21])
    assert not detect_task_hint(SQLITE_PROJECT_REQUESTS[23])


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("sequence", [17, 19, 21])
async def test_original_project_requests_dispatch_before_analysis(automatic, stream, sequence):
    from unittest.mock import AsyncMock

    from Sprout.message.models import Message

    runtime, session = automatic.rig.runtime, automatic.rig.session
    workspace, principal = automatic.rig.workspace, automatic.rig.principal
    await runtime._remember_session_workspace(session.id, workspace.id)
    analyze, prepare = AsyncMock(return_value=None), AsyncMock(return_value=None)
    request = Message(SQLITE_PROJECT_REQUESTS[sequence], session_id=session.id, channel="cli")
    options = dict(principal=principal, foreground=True,
                   analysis_task_provider=analyze, code_task_provider=prepare)
    if stream:
        reply = next(c.outbound for c in [
            c async for c in runtime.handle_stream(request, **options)
        ] if c.outbound)
    else:
        reply = await runtime.handle(request, **options)
    assert reply.content == ("test_execution_cancelled" if sequence == 17
                             else "code_task_cancelled")
    analyze.assert_not_awaited()
    prepare.assert_awaited_once()
    assert runtime.session_workspace_id(prepare.call_args.kwargs["session"]) == workspace.id


DELIVERY_CODING_REQUEST = (
    "请分析当前项目，读取 SPEC.md，然后修复 delivery.py 中的配送费计算，"
    "不要修改规格和现有测试。运行 pytest 并完成验证、审查、写回和复测。"
)


@pytest.mark.parametrize("stream", [False, True])
async def test_analysis_then_edit_enters_coding_provider(automatic, stream):
    from unittest.mock import AsyncMock

    from Sprout.message.models import Message

    runtime, session = automatic.rig.runtime, automatic.rig.session
    workspace, principal = automatic.rig.workspace, automatic.rig.principal
    await runtime._remember_session_workspace(session.id, workspace.id)
    analyze = AsyncMock(side_effect=AssertionError("read-only analysis took the edit"))
    prepare = AsyncMock(return_value=None)
    message = Message(DELIVERY_CODING_REQUEST, session_id=session.id, channel="cli")
    options = dict(principal=principal, foreground=True, analysis_task_provider=analyze,
                   code_task_provider=prepare)
    if stream:
        reply = next(chunk.outbound for chunk in [
            chunk async for chunk in runtime.handle_stream(message, **options)
        ] if chunk.outbound)
    else:
        reply = await runtime.handle(message, **options)
    assert reply.content == "code_task_cancelled"
    analyze.assert_not_awaited()
    prepare.assert_awaited_once()
    assert prepare.call_args.kwargs["message"].content == DELIVERY_CODING_REQUEST


@pytest.mark.parametrize("terminal", [False, True])
async def test_repl_wires_composite_request_to_coding_preparation(cli, monkeypatch, terminal):
    from unittest.mock import AsyncMock

    from Sprout.cli import code_task

    runtime, session = cli.rig.runtime, cli.rig.session
    await runtime._remember_session_workspace(session.id, cli.rig.workspace.id)
    cli.state.inputs = [DELIVERY_CODING_REQUEST]
    cli.state.terminal = terminal
    prepare = AsyncMock(return_value=None)
    monkeypatch.setattr(code_task, "prepare", prepare)

    await cli.run()

    prepare.assert_awaited_once()
    assert prepare.call_args.args == (runtime, cli.rig.workspace.id)
    assert prepare.call_args.kwargs["message"].content == DELIVERY_CODING_REQUEST
    assert "code_task_cancelled" in cli.state.display
    assert not cli.state.prompts


@pytest.mark.parametrize("instruction", [
    "只分析项目，读取 SPEC.md，不要修改任何文件。",
    "先分析项目，然后修改方案。",
    "分析项目，读取 SPEC.md，然后修复 delivery.py。取消本次任务。",
    "分析项目，然后修复 delivery.py 吗？",
])
def test_inspection_does_not_admit_read_only_or_ambiguous_requests(instruction):
    assert not detect_task_hint(instruction)


@pytest.mark.parametrize("approved", [False, True])
async def test_foreground_git_uses_exact_task_capability_without_prompt(approved):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.runtime.foreground_git_coding import ForegroundGitCoding
    from Sprout.security.approval import ApprovalStatus

    before = ApprovalStatus.PENDING if approved else ApprovalStatus.REJECTED
    grant = SimpleNamespace(id="grant", status=before, tool="coding_task_capability_v1")
    settled = SimpleNamespace(status=ApprovalStatus.APPROVED, tool=grant.tool)
    manager = SimpleNamespace(coding_task_capability=AsyncMock(return_value=grant),
                              decide=AsyncMock(return_value=settled))
    assembly = object.__new__(ForegroundGitCoding)
    assembly.runtime = SimpleNamespace(approvals=manager)
    assembly.commands = SimpleNamespace(_binding="frozen-binding")
    actor = SimpleNamespace(user_id="operator")
    task = SimpleNamespace(actor=actor)
    node = SimpleNamespace(id="verification")
    assembly.subject = AsyncMock(return_value=task)

    result = await assembly.approval(manager, task=task, node=node)

    manager.coding_task_capability.assert_awaited_once_with(
        task, "frozen-binding", request_approval=False)
    assert result is (settled if approved else grant)
    if approved:
        manager.decide.assert_awaited_once_with(
            "grant", True, principal=actor, decided_by="operator", channel="cli",
            reason="Foreground Git isolated Coding")
    else:
        manager.decide.assert_not_awaited()


async def test_cli_prefers_settled_apply_evidence_to_the_agent_draft(capsys):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.cli.commands import chat
    from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
    from Sprout.task.models import TaskStatus

    agent = ExecutionNode(task_id="task", type=NodeType.AGENT, status=NodeStatus.COMPLETED,
                          metadata={"output": {"content": "I could not execute pytest."}})
    apply = ExecutionNode(task_id="task", type=NodeType.APPLY, status=NodeStatus.COMPLETED,
                          metadata={"execution_guard": {"state": "completed"}, "output": {
                              "applied": True, "transaction_id": "transaction", "verification": {
                                  "verified": True,
                                  "pytest": {"results": [{
                                      "exit_code": 0, "passed": True,
                                      "output": "........ [100%]\n13 passed in 0.05s\n",
                                  }]},
                                  "cases": {"results": [
                                      {"kind": "acceptance", "passed": True},
                                      {"kind": "regression", "passed": True},
                                      {"kind": "poc", "passed": True},
                                  ]},
                              },
                          }})
    runtime = SimpleNamespace(
        get_task=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.COMPLETED)),
        storage=SimpleNamespace(metadata=SimpleNamespace(
            list_execution_nodes=AsyncMock(return_value=[agent, apply]))),
    )

    await chat._continue_foreground_task(runtime, {"task_id": "task"}, "operator")

    output = capsys.readouterr().out
    assert "13 passed in 0.05s" in output
    assert "Acceptance 1/1" in output
    assert "I could not execute pytest" not in output


def test_delivery_plan_keeps_the_spec_and_tests_out_of_write_scope():
    plan = plan_files(("delivery.py", "SPEC.md", "tests/test_delivery.py"),
                      DELIVERY_CODING_REQUEST)
    assert plan["writes"] == ("delivery.py",)
    assert plan["execution"] == ("run_tests",)


async def test_cli_failed_verification_uses_runtime_state_instead_of_agent_draft(capsys):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.cli.commands import chat
    from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
    from Sprout.task.models import TaskStatus

    agent = ExecutionNode(task_id="task", type=NodeType.AGENT, status=NodeStatus.COMPLETED,
                          metadata={"output": {"content": "I already wrote back the file."}})
    runtime = SimpleNamespace(
        get_task=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.FAILED)),
        storage=SimpleNamespace(metadata=SimpleNamespace(
            list_execution_nodes=AsyncMock(return_value=[agent]))),
    )
    await chat._continue_foreground_task(runtime, {"task_id": "task"}, "operator")
    output = capsys.readouterr().out
    assert "failed" in output
    assert "already wrote back" not in output
    assert "写回" in output or "write-back" in output


DISCOVER_AND_REPAIR = (
    "检查当前项目，结合项目规格和现有测试，找出一个可复现的功能问题。"
    "如果发现问题，直接修复并运行验证；验证通过后写回项目，再复测一次。"
    "保留现有规格和测试。如果没有发现可复现的问题，就说明检查了什么，不要改动文件。"
)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("exit_code, failures, status, spec_mismatch, repair", [
    (0, 0, "passed", False, False), (0, 0, "passed", True, True),
    (1, 1, "failed", False, True), (2, 0, "failed", False, False),
])
async def test_conditional_repair_requires_reproduced_failure(
    automatic, monkeypatch, stream, exit_code, failures, status, spec_mismatch, repair,
):
    from unittest.mock import AsyncMock

    from Sprout.message.models import Message, OutboundMessage

    runtime, session = automatic.rig.runtime, automatic.rig.session
    workspace, principal = automatic.rig.workspace, automatic.rig.principal
    await runtime._remember_session_workspace(session.id, workspace.id)
    baseline_result = {"task_id": "baseline", "task_status": "completed",
                       "tests": {"exit_code": exit_code, "failed": failures, "status": status}}
    probe_result = {**baseline_result, "spec_probe": {"results": [
        {"kind": "acceptance", "allowed": True, "exit_code": 0,
         "passed": not spec_mismatch, "actual_output": 10},
        {"kind": "regression", "allowed": True, "exit_code": 0, "passed": True},
    ], "verified": not spec_mismatch}}
    baseline = AsyncMock(side_effect=[baseline_result, probe_result])
    monkeypatch.setattr("Sprout.runtime.isolated_test_run.run_prepared", baseline)
    analyze = AsyncMock(side_effect=AssertionError("unconditional analysis route"))
    from types import SimpleNamespace

    bundle = {"bundle_sha256": "frozen"}
    prepare = AsyncMock(return_value=SimpleNamespace(
        id="baseline", workspace_id=workspace.id, metadata={"filesystem_spec_probe_v1": bundle},
    ))
    coding = AsyncMock(return_value=OutboundMessage(
        "coding started", "cli", session.id, "request", {"intent": "task"}))
    monkeypatch.setattr(runtime, "_handle_task", coding)
    message = Message(DISCOVER_AND_REPAIR, session_id=session.id, channel="cli")
    options = dict(principal=principal, foreground=True,
                   analysis_task_provider=analyze, code_task_provider=prepare)
    if stream:
        reply = next(chunk.outbound for chunk in [
            chunk async for chunk in runtime.handle_stream(message, **options)
        ] if chunk.outbound)
    else:
        reply = await runtime.handle(message, **options)
    assert baseline.await_count == (2 if status == "passed" else 1)
    analyze.assert_not_awaited()
    assert execution_request(prepare.await_args_list[0].kwargs["message"].content)
    if status == "passed":
        assert prepare.await_args_list[1].kwargs["spec_probe"] is True
    if repair:
        coding.assert_awaited_once()
        assert coding.call_args.args[0].content == DISCOVER_AND_REPAIR
        if spec_mismatch:
            repair_provider = coding.call_args.kwargs["code_task_provider"]
            await repair_provider(message=message, session=session, principal=principal)
            frozen, observed = prepare.await_args.kwargs["reproduced_spec"]
            assert frozen is bundle
            assert observed == probe_result["spec_probe"]
    else:
        coding.assert_not_awaited()
        assert reply.metadata["test_execution"]["exit_code"] == exit_code


def test_multimodule_probe_can_check_all_sources_before_narrowing_write_plan():
    paths = ("checkout.py", "shipping.py", "tests/test_checkout.py", "SPEC.md")
    plan = plan_files(paths, DISCOVER_AND_REPAIR, spec_probe=True)
    assert plan["reads"] == tuple(sorted(paths))
    assert plan["writes"] == ("checkout.py", "shipping.py")
    narrowed = plan_files(paths, DISCOVER_AND_REPAIR, repair_targets=("shipping.py",))
    assert narrowed["writes"] == ("shipping.py",)


def test_conditional_repair_plan_requires_a_single_source_target():
    assert plan_files(("billing.py", "tests/test_billing.py", "SPEC.md"),
                      DISCOVER_AND_REPAIR)["writes"] == ("billing.py",)
    with pytest.raises(PermissionError, match="filesystem_planning_ambiguous_source_target"):
        plan_files(("billing.py", "unrelated.py", "tests/test_billing.py"), DISCOVER_AND_REPAIR)


def test_conditional_repair_does_not_override_cancellation():
    from Sprout.runtime.isolated_test_run import conditional_repair_request

    assert conditional_repair_request(DISCOVER_AND_REPAIR)
    assert not conditional_repair_request("只分析项目，不修改任何文件。")
    assert not conditional_repair_request(DISCOVER_AND_REPAIR + "取消本次任务。")
    assert not conditional_repair_request("不要改动任何文件。" + DISCOVER_AND_REPAIR)
    assert not conditional_repair_request(
        DISCOVER_AND_REPAIR.replace("如果发现问题", "不要改动文件。如果发现问题")
    )


@pytest.mark.parametrize("choice, error", [
    ({"path": "shipping.py", "line": 2}, None),
    ({"path": "unrelated.py", "line": 2}, "not_supported_by_evidence"),
    ({"path": "shipping.py", "line": 99}, "not_supported_by_evidence"),
    ({}, "failed_test_unmapped"),
    (None, "baseline_changed"),
])
async def test_multimodule_repair_target_uses_failed_test_dependency_chain(choice, error):
    import json
    from unittest.mock import AsyncMock

    from Sprout.cli.repair_target import select_repair_targets
    from Sprout.llm.messages import LLMResponse
    from Sprout.workspace.authorized_content import AuthorizedEntry

    contents = {
        "tests/test_checkout.py": b"from checkout import total",
        "checkout.py": b"from shipping import fee\ndef total(): return fee()",
        "shipping.py": b"def fee():\n    return 0",
        "unrelated.py": b"def other():\n    return 0",
    }
    entries = [AuthorizedEntry.synthetic(path, data) for path, data in contents.items()]
    manifest = {e.relative_path: e.digest for e in entries}
    if choice is None:
        manifest["shipping.py"] = "stale"
    evidence = {"task_status": "completed", "input_manifest": manifest,
                "tests": {"framework": "pytest", "exit_code": 1,
                          "stdout": "FAILED tests/test_checkout.py::test_remote - assert 0 == 3"}}
    model = AsyncMock()
    reply = {"tests/test_checkout.py::test_remote": [choice]} if choice else {}
    model.chat.return_value = LLMResponse(content=json.dumps(reply))
    args = (model, DISCOVER_AND_REPAIR, list(contents), (), entries, evidence)
    if error:
        with pytest.raises(PermissionError, match=error):
            await select_repair_targets(*args)
    else:
        targets = await select_repair_targets(*args)
        assert targets == ("shipping.py",)
        plan = plan_files(contents, DISCOVER_AND_REPAIR, repair_targets=targets)
        assert plan["writes"] == targets
        prompt = json.loads(model.chat.call_args.args[0][1].content)
        assert set(prompt["candidate_sources"]) == {"checkout.py", "shipping.py"}
        assert "assert 0 == 3" in prompt["pytest_failure"]


async def test_pytest_repair_retains_one_root_cause_per_failed_test():
    import json
    from unittest.mock import AsyncMock

    from Sprout.cli.repair_target import select_repair_targets
    from Sprout.llm.messages import LLMResponse
    from Sprout.workspace.authorized_content import AuthorizedEntry

    entries = [AuthorizedEntry.synthetic(path, source) for path, source in {
        "tests/test_totals.py": b"from totals import total",
        "tests/test_fees.py": b"from fees import fee",
        "totals.py": b"from fees import fee\ndef total(): return fee()",
        "fees.py": b"def fee(): return 0",
    }.items()]
    failure = {"task_status": "completed",
               "input_manifest": {entry.relative_path: entry.digest for entry in entries},
               "tests": {"framework": "pytest", "exit_code": 1, "stdout":
                         "FAILED tests/test_totals.py::test_member - assert 0 == 1\n"
                         "FAILED tests/test_fees.py::test_remote - assert 0 == 3"}}
    model = AsyncMock()
    model.chat.return_value = LLMResponse(content=json.dumps({
        "tests/test_totals.py::test_member": [{"path": "totals.py", "line": 2}],
        "tests/test_fees.py::test_remote": [{"path": "fees.py", "line": 1}],
    }))
    args = (model, DISCOVER_AND_REPAIR, list(failure["input_manifest"]), (), entries, failure)
    assert await select_repair_targets(*args) == ("fees.py", "totals.py")
    model.chat.return_value = LLMResponse(
        content='{"tests/test_totals.py::test_member":[{"path":"totals.py","line":2}]}',
    )
    with pytest.raises(PermissionError, match="filesystem_repair_failed_test_unmapped"):
        await select_repair_targets(*args)


def test_spec_probe_needs_observed_acceptance_mismatch_and_passing_regression():
    from Sprout.runtime.isolated_test_run import spec_probe_found_defect

    failure = {"kind": "acceptance", "passed": False, "allowed": True, "exit_code": 0,
               "outcome": "check_failed", "actual_output": 10}
    regression = {"kind": "regression", "passed": True, "allowed": True, "exit_code": 0}
    demo = {**regression, "kind": "poc"}
    assert spec_probe_found_defect({"results": [failure, regression, demo]})
    assert not spec_probe_found_defect({"results": [failure, demo]})
    assert not spec_probe_found_defect({"results": [failure, {**regression, "passed": False}]})
    assert not spec_probe_found_defect({"results": [
        {**failure, "exit_code": 2}, regression, demo,
    ]})


def test_composite_plan_preserves_edit_and_test_targets():
    plan = plan_files(("app.py", "test_app.py"),
                      "修改代码、补充测试、运行测试、修复失败的测试，最后 Review。")
    assert plan["writes"] == ("app.py", "test_app.py")
    assert plan["execution"] == ("run_tests",)
    assert "test_extension" in plan["actions"]


def test_source_review_routes_without_writes():
    from Sprout.message.models import Message
    from Sprout.runtime.analysis_consent import explicit_request
    from Sprout.runtime.runtime import _project_analyze_tool_call

    text = "检查代码并 Review。"
    assert explicit_request(text)
    assert not detect_task_hint(text)
    assert _project_analyze_tool_call(Message(text), "workspace") is not None


def test_daily_feature_request_routes_to_coding():
    assert detect_task_hint(
        "为当前项目新增优先标签功能。根据项目规格，在新模块中实现共用的优先标签逻辑，"
        "接入包裹和收据两个入口，补充测试并保留默认行为。运行全部测试，验证通过后写回并复测。"
    )
    assert not detect_task_hint("讨论如何为当前项目新增优先标签功能。")
    assert not detect_task_hint("为当前项目新增优先标签功能。取消本次任务，不要执行。")


async def test_structural_plan_preserves_source_and_test_operations():
    import json
    from unittest.mock import AsyncMock

    from Sprout.cli.filesystem_preparation import _plan_structural_edit
    from Sprout.llm.messages import LLMResponse
    from Sprout.workspace.authorized_content import AuthorizedEntry
    from Sprout.workspace.models import ResourceKind

    source = "src/catalog/labels.py"
    test = "tests/test_labels.py"
    new = "src/catalog/priority.py"
    entries = [
        AuthorizedEntry.synthetic(source, b"def label(code): return code"),
        AuthorizedEntry.synthetic(test, b"def test_label(): pass", ResourceKind.TEST),
    ]
    model = AsyncMock()
    model.chat.return_value = LLMResponse(content=json.dumps({
        "update": [source, test], "create": [new],
    }))
    request = "新增标签功能，在新模块中实现。补充测试。"
    plan = await _plan_structural_edit(model, request, [source, test], (), entries)
    assert plan["writes"] == tuple(sorted([source, test, new]))
    assert plan["reads"] == (source, test)
    assert plan["execution"] == ("run_tests",)
    with pytest.raises(PermissionError, match="filesystem_planning_target_unresolved"):
        await _plan_structural_edit(model, "重构标签，保留现有测试。", [source, test], (), entries)


@pytest.mark.parametrize(("manifest", "language"), [
    ("package.json", "Node/TypeScript"), ("go.mod", "Go"),
])
def test_non_python_project_reports_its_missing_adapter(tmp_path, manifest, language):
    from Sprout.cli.filesystem_task import _unavailable_language

    (tmp_path / manifest).touch()
    assert _unavailable_language(tmp_path) == language
    (tmp_path / "pyproject.toml").touch()
    assert _unavailable_language(tmp_path) is None
