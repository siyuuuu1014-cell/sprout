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
    from Sprout.message.models import Message
    from Sprout.workspace.models import Workspace

    principal = object()
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
