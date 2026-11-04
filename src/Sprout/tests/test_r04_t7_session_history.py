"""Owned persisted task observations are queries, not new project execution."""
import json
from dataclasses import replace

import pytest

from Sprout.execution.models import ChangeProposal
from Sprout.gateway.identity import Principal
from Sprout.message.models import Message
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.rootstock import create_session_store
from Sprout.runtime.dialogue import code_status, history_question
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus
from Sprout.tests.conftest import build_runtime


@pytest.mark.parametrize("text", [
    "我们上一轮修改了什么？测试是否通过？",
    "我们上一轮修改了什么？测试是否通过？请结合此Session的持久化任务、验证和Apply结果回答，"
    "并说明当前Workspace；不要重新执行任务或修改文件。",
    "刚才的任务完成了吗？", "Apply 是否成功？", "当前 Workspace 是哪个？",
    "上次任务为什么失败？",
])
def test_history_query(text):
    assert history_question(text)


@pytest.mark.parametrize("text", [
    "分析当前项目的入口和依赖", "继续解释刚才分析的核心模块",
    "修复 src/app.py 并运行测试", "重新执行上次任务", "分析当前项目并说明测试是否通过",
])
def test_new_work_is_not_history(text):
    assert not history_question(text)


@pytest.mark.parametrize("outcome", ["passed", "failed", "missing"])
async def test_owned_history_real_sqlite_reopen(tmp_path, outcome):
    path = tmp_path / "history.db"
    storage = StorageBundle.in_memory()
    storage.metadata = open_metadata_store(path)
    storage.sessions = create_session_store("sqlite:///" + str(tmp_path / "sessions.db"))
    runtime, _ = build_runtime(storage=storage)
    principal = Principal(user_id="owner", source="cli", authenticated=True)
    session = await runtime.create_session("owner", principal=principal)
    workspace = await runtime.open_workspace(str(tmp_path))
    session = replace(session, metadata={**session.metadata,
        "workspace_id": workspace.id, "last_code_task_id": "history-task"})
    # Use the existing binding method's actual metadata contract.
    await runtime._session_store.save_session(session)
    await runtime._remember_session_workspace(session.id, workspace.id)
    session = await runtime.get_session(session.id)
    task = Task(id="history-task", workspace_id=workspace.id, actor=principal, source="cli",
                status=TaskStatus.COMPLETED, metadata={"session_id": session.id})
    await storage.metadata.save_task(task)
    if outcome != "missing":
        output = {"status": "passed" if outcome == "passed" else "failed", "results": [
            {"name": "verification: pytest", "passed": outcome == "passed",
             "executed": True, "command": ["python", "-I", "-c", "x" * 10000],
             "exit_code": 0 if outcome == "passed" else 1}]}
        output["test_results"] = [{"name": "verification: pytest",
            "passed": outcome == "passed", "executed": True, "output": "fixture result"}]
        node = ExecutionNode(task_id=task.id, type=NodeType.EVALUATION,
                             status=NodeStatus.COMPLETED, metadata={"output": output})
        await storage.metadata.save_execution_node(node)
    # These are historical observations, never Receipt/Guard authorization.
    data = json.loads((await code_status(runtime, session, principal)).split("\n", 1)[1])
    assert data["workspace"] == {"id": workspace.id, "root": str(tmp_path), "bound": True}
    assert data["session_id"] == session.id
    if outcome == "missing":
        assert data["verification"]["status"] == "NOT_AVAILABLE"
    else:
        assert data["verification"]["checks"][0]["passed"] is (outcome == "passed")
        assert len(json.dumps(data)) < 5000
        assert "argv_sha256" in data["verification"]["checks"][0]["command"]
    pending = ChangeProposal(task_id=task.id, files_changed=("src/app.py",))
    await storage.metadata.save_change_proposal(pending)
    data = json.loads((await code_status(runtime, session, principal)).split("\n", 1)[1])
    assert data["proposals"][0]["status"] == "pending"
    assert data["proposals"][0]["apply_phase"] == "NOT_STARTED"
    assert data["proposals"][0]["receipt_sha256"] == "NOT_AVAILABLE"
    other = await runtime.create_session("owner", principal=principal)
    assert await code_status(runtime, other, principal) is None
    # Wrong owner cannot query the selected task, even with a copied reference.
    assert await code_status(runtime, session, Principal(
        user_id="other", source="cli", authenticated=True)) is None
    message = Message("我们上一轮修改了什么？测试是否通过？", user_id="owner",
                      session_id=session.id)
    context = await runtime._ordinary_conversation_context(message, session, {}, {})
    assert context.metadata["session_task_history_query"]
    classification = await runtime._classify_intent_with_llm(message, context)
    assert classification["reason"] == "owned_session_task_history"
    before = await storage.metadata.list_execution_nodes(task.id)
    assert await runtime._dispatch_project_analysis(message, session, principal=principal,
        analysis_task=None, classification={"intent": "workspace"},
        analysis_task_provider=lambda **kw: pytest.fail("Must not select project files")) is None
    assert await storage.metadata.list_execution_nodes(task.id) == before
    await runtime.stop()
    await storage.close()
    reopened = StorageBundle.in_memory()
    reopened.metadata = open_metadata_store(path)
    reopened.sessions = create_session_store("sqlite:///" + str(tmp_path / "sessions.db"))
    restarted, _ = build_runtime(storage=reopened)
    restored = await restarted.get_session(session.id)
    assert restarted.session_workspace_id(restored) == workspace.id
    restored_facts = await code_status(restarted, restored, principal)
    assert json.loads(restored_facts.split("\n", 1)[1]) == data
    await restarted.stop()
    await reopened.close()
