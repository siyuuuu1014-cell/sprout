"""Exact file scopes, one captured view, actual REPL and ToolExecutor.

Uses reviewed controlled terminal/identity/model/secure-open fixtures, not OS evidence.
"""
import json
from contextlib import contextmanager
from copy import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from Sprout.message.models import Message
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.security.engine import PolicyEngine
from Sprout.task.models import TaskExecutionMode
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b4_cli_analysis import cli as cli
from Sprout.tests.test_r04_pa2_b1_binding import node_scope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig

FILES = ("src/main.py", "src/worker.py")


@pytest.fixture
def results(cli, monkeypatch):
    analyses, views = [], []
    original = cli.rig.runtime._workspaces.analyze_view

    async def analyze(workspace, task, view):
        views.append(view)
        result = await original(workspace, task, view)
        analyses.append(result)
        return result
    monkeypatch.setattr(cli.rig.runtime._workspaces, "analyze_view", analyze)
    return analyses, views


def assert_whole_analysis(cli, results):
    analyses, views = results
    assert len(analyses) == len(views) == 1
    analysis = analyses[0]
    assert set(analysis.fingerprint.resource_hashes) == set(FILES)
    assert any(edge.relation == "imports" for edge in analysis.graph.edges)
    names = {node.name for node in analysis.graph.nodes}
    assert "src/main.py:main" in names and "src/worker.py:work" in names
    assert cli.captured.opens == list(FILES)
    assert any(str(m.role) == "tool" and "main" in m.content for m in cli.state.seen)


async def task_for(cli):
    return await cli.rig.runtime.create_task(
        cli.rig.workspace.id, "analyze selected files", actor=cli.rig.principal, source="cli",
        execution_mode=TaskExecutionMode.READ_ONLY,
        delegation_scope=replace(cli.rig.task.delegation_scope,
            allowed_paths=tuple(str(cli.rig.root / p) for p in FILES)),
        metadata={"session_id": cli.rig.session.id, "required_paths": list(FILES)})


async def test_host_exact_files_one_view_not_parent_scope(cli, results):
    task = await task_for(cli)
    assert not task.delegation_scope.permits("tool.invoke", str(cli.rig.root / "src"))
    result = await cli.rig.runtime.handle(
        Message("请分析项目结构", session_id=cli.rig.session.id),
        principal=cli.rig.principal, analysis_task=task)
    assert result.metadata["analysis_status"] == "ok", result.content
    assert_whole_analysis(cli, results)


async def test_real_repl_json_manifest_one_task_two_exact_files(cli, results):
    cli.state.answers = [json.dumps(FILES), True]
    await cli.run()
    cli.create.assert_awaited_once()
    scope = cli.create.call_args.kwargs["delegation_scope"]
    assert scope.allowed_paths == tuple(str(cli.rig.root / p) for p in FILES)
    assert not scope.permits("file.read", str(cli.rig.root / "src"))
    assert cli.state.classification == 1 and len(cli.state.prompts) == 2
    assert "CLI_PROJECT_MAIN_RESULT" in cli.state.display
    assert_whole_analysis(cli, results)


@pytest.mark.parametrize("action", [ActionType.TOOL_INVOKE, ActionType.FILE_READ])
@pytest.mark.parametrize("decision", [AccessDecision.DENY, AccessDecision.REQUIRE_APPROVAL])
async def test_second_member_cannot_borrow_entry_allow(cli, results, monkeypatch, action, decision):
    requests = []
    class Policy:
        def decide(self, request):
            requests.append(request)
            base = PolicyEngine().decide(request)
            if (request.action is action and request.resource is not None
                    and request.resource.path.replace("\\", "/").endswith("/src/worker.py")):
                return PolicyDecision(decision, "second member denied")
            return base
    policy = Policy()
    monkeypatch.setattr(cli.rig.runtime, "_policy_engine", policy)
    monkeypatch.setattr(cli.rig.executor, "policy_engine", policy)
    task = await task_for(cli)
    approvals = await cli.rig.storage.operational.list_approvals()
    result = await cli.rig.runtime.handle(
        Message("请分析项目结构", session_id=cli.rig.session.id),
        principal=cli.rig.principal, analysis_task=task)
    assert result.metadata["analysis_status"] == "unavailable"
    assert not cli.captured.opens and not results[0]
    assert any(r.action is action and r.resource.path.endswith("worker.py") for r in requests)
    assert await cli.rig.storage.operational.list_approvals() == approvals
    assert "src/worker.py:work" not in repr(cli.state.seen)


@pytest.mark.parametrize("change", ["expired", "narrowed"])
async def test_scope_changed_between_reads_discards_whole_batch(cli, results, monkeypatch, change):
    task = await task_for(cli)
    original = cli.rig.runtime._file_broker.read_snapshot

    async def read(*args, **kwargs):
        result = await original(*args, **kwargs)
        current = await cli.rig.storage.metadata.get_task(task.id)
        scope = current.delegation_scope
        scope = (replace(scope, expires_at=datetime.now(UTC) - timedelta(seconds=1))
                 if change == "expired" else replace(
                     scope, allowed_paths=(scope.allowed_paths[0],)))
        await cli.rig.storage.metadata.save_task(replace(current, delegation_scope=scope))
        return result
    monkeypatch.setattr(cli.rig.runtime._file_broker, "read_snapshot", read)
    result = await cli.rig.runtime.handle(
        Message("请分析项目结构", session_id=cli.rig.session.id),
        principal=cli.rig.principal, analysis_task=task)
    assert result.metadata["analysis_status"] == "unavailable"
    assert cli.captured.opens == [FILES[0]] and not results[0]
    assert "def main" not in repr(cli.state.seen)


async def test_identity_failure_on_second_file_has_no_partial_analysis(cli, results, monkeypatch):
    from Sprout.execution import file_broker
    from Sprout.execution.file_access import FileBoundaryError

    original = file_broker.file_operation
    def boundary(root, target, *args, **kwargs):
        if target.name == "worker.py":
            raise FileBoundaryError("snapshot_identity_changed")
        return original(root, target, *args, **kwargs)
    monkeypatch.setattr(file_broker, "file_operation", boundary)
    task = await task_for(cli)
    result = await cli.rig.runtime.handle(
        Message("请分析项目结构", session_id=cli.rig.session.id),
        principal=cli.rig.principal, analysis_task=task)
    assert result.metadata["analysis_status"] == "unavailable"
    assert cli.captured.opens == [FILES[0]] and not results[0]


async def test_revocation_after_unified_analysis_prevents_publication(cli, results, monkeypatch):
    task = await task_for(cli)
    original = cli.rig.runtime._workspaces.analyze_view
    async def analyze(*args):
        result = await original(*args)
        current = await cli.rig.storage.metadata.get_task(task.id)
        await cli.rig.storage.metadata.save_task(replace(current, delegation_scope=replace(
            current.delegation_scope, denied_paths=(str(cli.rig.root / FILES[1]),))))
        return result
    monkeypatch.setattr(cli.rig.runtime._workspaces, "analyze_view", analyze)
    result = await cli.rig.runtime.handle(
        Message("请分析项目结构", session_id=cli.rig.session.id),
        principal=cli.rig.principal, analysis_task=task)
    assert result.metadata["analysis_status"] == "unavailable"
    assert len(results[0]) == 1
    assert "src/worker.py:work" not in repr(cli.state.seen)


@contextmanager
def manifest_scope(cli):
    rig = cli.rig
    rig.runtime._online_analysis_requests[rig.node.id] = {"paths": FILES}
    try:
        with node_scope(rig):
            yield
    finally:
        rig.runtime._online_analysis_requests.pop(rig.node.id, None)


async def exact_running_subject(cli):
    rig = cli.rig
    rig.task = replace(rig.task, delegation_scope=replace(rig.task.delegation_scope,
        allowed_paths=tuple(str(rig.root / p) for p in FILES)))
    await rig.storage.metadata.save_task(rig.task)


@pytest.mark.parametrize("attack", ["fake_list", "path", "copy", "other_runtime"])
async def test_bound_manifest_cannot_be_expanded_or_transplanted(cli, monkeypatch, attack):
    await exact_running_subject(cli)
    rig = cli.rig
    with manifest_scope(cli):
        tool = await rig.runtime._issue_project_analysis_tool(paths=FILES)
        source = tool
        from Sprout.tools.project_analyze_tool import ProjectAnalyzeTool

        assert tool.spec.input_schema == ProjectAnalyzeTool().spec.input_schema
        if attack == "copy":
            tool = copy(tool)
        executor = rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
        invoke = AsyncMock(wraps=tool.invoke)
        monkeypatch.setattr(tool, "invoke", invoke)
        arguments = {"path": str(rig.root / FILES[0]), "paths": ["src/private/denied.py"],
                     "metadata": {"manifest": ["src/private/denied.py"], "trusted": True}}
        if attack == "path":
            arguments["path"] = str(rig.root / "src")
        if attack == "other_runtime":
            other = copy(rig.runtime)
            with other._project_analysis_scope(rig.task, rig.workspace, rig.node):
                result = await executor.execute(tool.spec.name, arguments)
        else:
            result = await executor.execute(tool.spec.name, arguments)
        assert result.ok is (attack == "fake_list")
        if attack == "fake_list":
            assert rig.runtime._project_manifest_tools.get(source) is not None
            assert cli.captured.opens == list(FILES)
        else:
            invoke.assert_not_called()
            assert not cli.captured.opens
    assert source not in rig.runtime._project_manifest_tools


async def test_normal_tool_keeps_path_filtering(cli):
    with node_scope(cli.rig):
        tool = await cli.rig.runtime._issue_project_analysis_tool(paths=FILES)
        assert tool not in cli.rig.runtime._project_manifest_tools
        executor = cli.rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
        result = await executor.execute(tool.spec.name, {"path": str(cli.rig.root / FILES[0])})
    assert result.ok and cli.captured.opens == [FILES[0]]


@pytest.mark.parametrize("raw,confirm", [
    ('["src/main.py", "src/worker.py"]', False),
    ('["src/main.py", "../outside.py"]', True),
    ('["src/main.py", "src/main.py"]', True),
    ('["src/main.py", "SRC/MAIN.PY"]', True),
    ('["src/main.py", 1]', True), ('[]', True), ('[broken', True),
])
async def test_cli_manifest_bad_or_cancelled_never_creates_task(cli, raw, confirm):
    cli.state.answers = [raw, confirm]
    await cli.run()
    cli.create.assert_not_called()
    cli.claim.assert_not_called()
    assert not cli.captured.opens and not cli.state.seen
