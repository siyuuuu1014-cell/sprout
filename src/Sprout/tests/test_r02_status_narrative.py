"""Task descriptions are not write instructions; use real routing, mocked effects."""

from unittest.mock import AsyncMock

import pytest

from Sprout.gateway.identity import Principal
from Sprout.intents import detect_task_hint
from Sprout.message.models import Message
from Sprout.tests.test_r02_trusted_intent_audit import audit_runtime as audit_runtime

STATUS_TEXTS = (
    "修复登录问题的任务目前仍未完成",
    "修改配置的工作还在排队",
    "重构缓存的需求仍然处于待办状态",
    "新增接口的任务处于暂停状态",
    "补充测试的工作早先做过了",
    "实现缓存的任务此前完成了",
    "修复登录问题的进展是百分之五十",
    "修改配置的任务剩余两项",
    "重构缓存的需求",
    "新增接口的计划",
    "修复登录问题目前仍未完成",
    "实现缓存可能需要排期",
    "fix the login bug task remains pending",
    "implement caching work is in progress",
    "refactor the cache ticket was completed last week",
    "add a cache request remains queued",
    "modify the parser task has been completed",
    "fix for the login bug",
    "refactor of the cache layer",
    "fixing the login bug remains pending",
    "implementation of the cache layer",
    "create a service task is paused",
    "fix the login bug: pending",
    "modify the parser task",
    "fix the login bug has been completed",
    "fix the login bug was completed last week",
    "修复登录问题目前处于待办状态",
    "修复登录问题的任务：待办",
    "修复登录问题仍在进行中",
    "修改配置目前正在排队",
    "重构缓存还没有开始",
    "新增接口的任务正在处理中",
)
COMMAND_TEXTS = (
    "修复登录问题",
    "请修复登录问题",
    "/task 修复登录问题",
    "please fix the login bug",
    "请修复尚未完成的登录问题",
    "修复尚未完成的登录问题",
    "请修改目前仍未完成的配置任务",
    "please fix the unfinished login task",
    "fix the pending login bug",
    "implement caching",
    "请帮我修复登录问题",
    "帮我在当前项目的根目录下写一个冒泡排序的代码",
    "重构缓存模块",
    "/task 修复登录问题的任务目前仍未完成",
    "请修复尚未完成的任务",
    "修复尚未完成的任务",
    "please fix the bug that is still pending",
    "fix the bug that was introduced last week",
    "write an endpoint",
    "add an endpoint",
    "create an endpoint",
    "修复仍在报错的任务",
    "请修复目前仍在报错的任务",
    "请修改还没有完成的配置任务",
    "修复目前仍未完成的任务",
)


async def deliver(runtime, text, stream, workspace):
    message = Message(text, metadata={"workspace_id": "audit-workspace"} if workspace else {})
    principal = Principal("status-test-operator", source="cli")
    if stream:
        return [chunk async for chunk in runtime.handle_stream(message, principal=principal)]
    return await runtime.handle(message, principal=principal)


def guard_effects(runtime):
    runtime._request_workspace = AsyncMock(wraps=runtime._request_workspace)
    runtime.open_workspace = AsyncMock()
    # audit_runtime has no models/tools and replaces submit/execute with mocks;
    # no real repo, process, worker, Apply, network or provider is reachable.
    assert not runtime.models.list() and not runtime.tools.list()


@pytest.mark.parametrize("text", STATUS_TEXTS, ids=[f"status-{i:02}" for i in range(32)])
def test_status_hint_is_not_a_write_instruction(text):
    assert detect_task_hint(text) is False


@pytest.mark.parametrize("text", COMMAND_TEXTS, ids=[f"command-{i:02}" for i in range(25)])
def test_explicit_command_hint_is_preserved(text):
    assert detect_task_hint(text) is True


@pytest.mark.parametrize("stream", [False, True], ids=["normal", "stream"])
@pytest.mark.parametrize("workspace", [False, True], ids=["unbound", "bound"])
@pytest.mark.parametrize("text", STATUS_TEXTS, ids=[f"status-{i:02}" for i in range(32)])
async def test_status_does_not_create_task_or_request_consent(
    audit_runtime, text, stream, workspace,
):
    runtime, saved, _ = audit_runtime
    guard_effects(runtime)
    await deliver(runtime, text, stream, workspace)
    assert saved == []
    runtime.create_task.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    runtime.execute.assert_not_awaited()
    runtime._request_workspace.assert_not_awaited()
    runtime.open_workspace.assert_not_awaited()


@pytest.mark.parametrize("stream", [False, True], ids=["normal", "stream"])
@pytest.mark.parametrize("workspace", [False, True], ids=["unbound", "bound"])
@pytest.mark.parametrize("text", COMMAND_TEXTS, ids=[f"command-{i:02}" for i in range(25)])
async def test_commands_keep_existing_task_and_consent_paths(
    audit_runtime, text, stream, workspace,
):
    runtime, saved, _ = audit_runtime
    guard_effects(runtime)
    await deliver(runtime, text, stream, workspace)
    if workspace:
        assert len(saved) == 1 and saved[0].instruction == text
        runtime.create_task.assert_awaited_once()
        runtime.submit_task.assert_awaited_once_with(saved[0])
        runtime._request_workspace.assert_not_awaited()
    else:
        assert saved == []
        runtime.create_task.assert_not_awaited()
        runtime.submit_task.assert_not_awaited()
        runtime._request_workspace.assert_awaited_once()
    runtime.execute.assert_not_awaited()
    runtime.open_workspace.assert_not_awaited()
    assert await runtime.pending_approvals() == []


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("workspace", [False, True])
async def test_model_task_label_does_not_authorize_status_text(audit_runtime, stream, workspace):
    runtime, saved, _ = audit_runtime
    guard_effects(runtime)
    runtime._classify_intent_with_llm = AsyncMock(return_value={"intent": "task"})
    await deliver(runtime, STATUS_TEXTS[0], stream, workspace)
    assert saved == []
    runtime.create_task.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    runtime.execute.assert_not_awaited()
    runtime._request_workspace.assert_not_awaited()
    runtime.open_workspace.assert_not_awaited()
