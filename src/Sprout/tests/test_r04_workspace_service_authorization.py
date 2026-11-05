"""Host service admission with real Metadata/Guard and controlled secure open.

Positive secure opens are a platform double, not native OS isolation evidence.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from Sprout.gateway.project_gateway import ProjectGateway
from Sprout.task.models import TaskExecutionMode
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


async def host_task(rig, paths=("src/main.py", "src/worker.py"), **changes):
    scope = replace(rig.task.delegation_scope,
                    allowed_paths=tuple(str(rig.root / p) for p in paths))
    task = await rig.runtime.create_task(
        rig.workspace.id, "understand selected project", actor=rig.principal,
        source=rig.principal.source,
        execution_mode=changes.pop("execution_mode", TaskExecutionMode.READ_ONLY),
        delegation_scope=scope, metadata={"required_paths": list(paths)},
    )
    task = replace(task, **changes)
    await rig.storage.metadata.save_task(task)
    return task


@pytest.mark.parametrize("case", ["valid", "missing", "unauthenticated", "workspace",
                                  "expired", "denied", "principal"])
async def test_host_analysis_current_authority(rig, captured, monkeypatch, case):
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    paths = ("src/private/denied.py",) if case == "denied" else (
        "src/main.py", "src/worker.py")
    task = await host_task(rig, paths)
    principal = rig.principal
    workspace = rig.workspace.id
    if case == "expired":
        task = replace(task, delegation_scope=replace(task.delegation_scope,
                       expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await rig.storage.metadata.save_task(task)
    if case == "unauthenticated":
        principal = replace(principal, authenticated=False)
    if case == "principal":
        principal = replace(principal, user_id="other")
    if case == "workspace":
        workspace = "other"
    if case == "missing":
        task = None
    if case == "valid":
        result = await rig.runtime.analyze_workspace(
            workspace, principal=principal, analysis_task=task)
        assert any(n.qualified_name.endswith("main") for n in result.graph.nodes), repr(
            result.graph.nodes)
        assert set(captured.opens) == set(paths)
        assert {r.path for r in result.read_plan.resources} <= set(paths)
    else:
        with pytest.raises(PermissionError):
            await rig.runtime.analyze_workspace(
                workspace, principal=principal, analysis_task=task)
        assert not captured.opens


@pytest.mark.parametrize("method", ["analyze", "scan", "analyze_path", "read_plan"])
async def test_legacy_service_has_no_disk_or_cache_fallback(rig, captured, method):
    value = {"analyze": rig.workspace.id, "scan": rig.workspace.id,
             "analyze_path": rig.root, "read_plan": rig.task.id}[method]
    # rig's effect spies assert that persistence and cache are never reached.
    service = rig.runtime._workspaces
    # The fixture intentionally replaces analyze/path; call the actual API.
    from Sprout.runtime.workspaces import WorkspaceService

    with pytest.raises(PermissionError):
        await getattr(WorkspaceService, method)(service, value)
    assert not captured.opens


@pytest.mark.parametrize("kind", ["graph", "knowledge", "symbols", "dependencies", "subgraph"])
async def test_gateway_query_uses_fresh_scoped_analysis(rig, captured, monkeypatch, kind):
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    task = await host_task(rig)
    gateway = ProjectGateway(rig.runtime, transport="sdk", default_user="not-authority")
    names = {"graph": "query_workspace_graph", "knowledge": "query_project_knowledge",
             "symbols": "query_workspace_symbols", "dependencies": "query_workspace_dependencies",
             "subgraph": "query_workspace_subgraph"}
    args = (rig.workspace.id,)
    if kind == "dependencies":
        args += ("src/main.py",)
    if kind == "subgraph":
        args += ("main",)
    result = await getattr(gateway, names[kind])(
        *args, principal=rig.principal, analysis_task=task)
    assert result is not None
    assert set(captured.opens) == {"src/main.py", "src/worker.py"}


@pytest.mark.parametrize("change", ["file_deny", "scope_shrink", "scope_expire"])
async def test_recheck_during_capture_does_not_publish(rig, captured, monkeypatch, change):
    from Sprout.security.access import AccessDecision, ActionType, PolicyDecision

    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    task = await host_task(rig)
    if change == "file_deny":
        original = rig.runtime._policy_engine

        class Deny:
            def decide(self, request):
                if request.action is ActionType.FILE_READ:
                    return PolicyDecision(AccessDecision.DENY, "controlled read deny")
                return original.decide(request)

        rig.runtime._policy_engine = Deny()
    else:
        original = rig.runtime._workspaces.analyze_view

        async def revoke(*args):
            scope = replace(task.delegation_scope, **(
                {"allowed_paths": (str(rig.root / "src/worker.py"),)}
                if change == "scope_shrink" else
                {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}))
            await rig.storage.metadata.save_task(replace(task, delegation_scope=scope))
            return await original(*args)

        monkeypatch.setattr(rig.runtime._workspaces, "analyze_view", revoke)
    with pytest.raises(PermissionError):
        await rig.runtime.analyze_workspace(
            rig.workspace.id, principal=rig.principal, analysis_task=task)
    if change == "file_deny":
        assert not captured.opens


@pytest.mark.parametrize("projection", ["graph", "knowledge", "fingerprint", "read_plan"])
async def test_real_sqlite_reopen_never_supplies_scoped_results(
    rig, captured, monkeypatch, tmp_path, projection,
):
    from Sprout.storage.local.sqlite.projects import SqliteProjectAnalysisStore
    from Sprout.workspace.authorized_content import AuthorizedContentView, AuthorizedEntry
    from Sprout.workspace.intelligence import WorkspaceIntelligence

    view = AuthorizedContentView(rig.workspace.id, (
        AuthorizedEntry.synthetic("hidden.py", b"def hidden_symbol():\n    return 42\n"),
        AuthorizedEntry.synthetic("pyproject.toml", b'[project]\nname="hidden-project"\n'),
    ))
    # Trusted fixture seeds historical DATA, not an authorization credential.
    broad = await WorkspaceIntelligence().analyze_view(rig.workspace, rig.task, view)
    root = tmp_path / "historical-snapshots"
    store = SqliteProjectAnalysisStore(root)
    await store.save_analysis(broad, expected_snapshot_id=None)
    store.close()
    reopened = SqliteProjectAnalysisStore(root)
    saved = await reopened.load_analysis(rig.workspace.id)
    assert saved is not None and "hidden" in repr(saved)
    load = AsyncMock(side_effect=AssertionError("historical snapshot accessed"))
    save = AsyncMock(side_effect=AssertionError("restricted result persisted"))
    monkeypatch.setattr(reopened, "load_analysis", load)
    monkeypatch.setattr(reopened, "save_analysis", save)
    rig.storage.projects = reopened
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    try:
        task = await host_task(rig, ("src/main.py",))
        result = await rig.runtime.analyze_workspace(
            rig.workspace.id, principal=rig.principal, analysis_task=task)
        assert "hidden" not in repr(getattr(result, projection))
        assert captured.opens == ["src/main.py"]
        load.assert_not_called()
        save.assert_not_called()
    finally:
        reopened.close()


@pytest.mark.parametrize("bound", [False, True])
async def test_actual_authenticated_web_requires_server_task(rig, captured, monkeypatch, bound):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from web.webapi.auth import WebAuth, WebAuthMiddleware
    from web.webapi.routes.project import create_project_routes

    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)

    async def provider(*, principal, workspace_id):
        assert principal.authenticated and principal.source == "web"
        scope = replace(rig.task.delegation_scope,
                        allowed_paths=(str(rig.root / "src/main.py"),))
        return await rig.runtime.create_task(
            workspace_id, "analyze", actor=principal, source=principal.source,
            execution_mode="read_only", delegation_scope=scope,
            metadata={"required_paths": ["src/main.py"]})

    app = Starlette(routes=create_project_routes(
        rig.runtime, analysis_task_provider=provider if bound else None))
    app.add_middleware(WebAuthMiddleware, auth=WebAuth(enabled=True, token="controlled-token"))
    with TestClient(app) as client:
        response = client.post(f"/api/project/workspaces/{rig.workspace.id}/analyze",
                               headers={"Authorization": "Bearer controlled-token"},
                               json={"task_id": rig.task.id, "scope": {"allowed_paths": ["/"]}})
    assert response.status_code == (200 if bound else 403), response.text
    if bound:
        assert response.json()["nodes"] > 0
        assert captured.opens == ["src/main.py"]
    else:
        assert not captured.opens


async def test_cli_explicit_selection_delivers_analysis(rig, captured, monkeypatch):
    from Sprout.cli.commands import project

    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    monkeypatch.setattr(project, "current_cli_principal", lambda: rig.principal)
    monkeypatch.setattr(project.typer, "prompt", lambda *a, **k: '["src/main.py","src/worker.py"]')
    monkeypatch.setattr(project.typer, "confirm", lambda *a, **k: True)
    gateway = ProjectGateway(rig.runtime, transport="cli", default_user="not-authority")
    context = await project._analysis_context(gateway, rig.workspace.id)
    result = await gateway.analyze_workspace(rig.workspace.id, **context)
    assert any(e.relation == "imports" for e in result.graph.edges)
    assert set(captured.opens) == {"src/main.py", "src/worker.py"}


@pytest.mark.parametrize("case_id", ["A01", "A02", "A03"])
async def test_eval_first_party_analysis_preserves_ground_truth(
    rig, captured, monkeypatch, tmp_path, case_id,
):
    from Sprout.evaluation import runner
    from Sprout.evaluation.fixtures import ANALYSIS_FILES, CASES
    from Sprout.tests.conftest import build_runtime

    for path, content in {**ANALYSIS_FILES, "pyproject.toml": '[project]\nname="eval"\n'}.items():
        target = rig.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    # A separate Runtime sharing the controlled Metadata fixture tests fresh
    # admission. Real database close/reopen is covered independently above.
    second, _ = build_runtime(storage=rig.storage)
    monkeypatch.setattr(runner, "_runtime", lambda *a: second)
    monkeypatch.setattr(rig.runtime, "stop", AsyncMock())
    monkeypatch.setattr(second, "stop", AsyncMock())
    case = next(case for case in CASES if case.id == case_id)
    result = await runner._analysis(
        rig.runtime, rig.workspace, case, tmp_path, principal=rig.principal)
    assert result["analysis_verified"] is True
    assert set(captured.opens) == set(ANALYSIS_FILES) | {"pyproject.toml"}


@pytest.mark.parametrize("bound", [False, True])
async def test_read_plan_requires_host_and_uses_only_captured_resources(
    rig, captured, monkeypatch, bound,
):
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    task = await host_task(rig, ("src/main.py",))
    if bound:
        plan = await rig.runtime.plan_task(task.id, principal=rig.principal)
        assert {r.path for r in plan.resources} == {"src/main.py"}
        assert captured.opens == ["src/main.py"]
    else:
        # This is also the unchanged MCP task_plan call: no implicit principal.
        with pytest.raises(PermissionError):
            await rig.runtime.plan_task(task.id)
        assert not captured.opens


async def test_code_read_plan_uses_separate_owner_without_mutating_code_graph(
    rig, captured, monkeypatch,
):
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    # Controlled repository marker satisfies create_task's existing admission;
    # no Git command runs and this is not a real CODE/Git integration claim.
    (rig.root / ".git").mkdir()
    task = await host_task(rig, ("src/main.py",), execution_mode=TaskExecutionMode.CODE)
    plan = await rig.runtime.plan_task(task.id, principal=rig.principal)
    current = await rig.storage.metadata.get_task(task.id)
    assert current == task
    assert not await rig.storage.metadata.list_execution_nodes(task.id)
    assert plan.task_id == task.id
    assert {r.path for r in plan.resources} == {"src/main.py"}
    others = [t for t in await rig.storage.metadata.list_tasks()
              if t.id not in {task.id, rig.task.id}]
    assert len(others) == 1
    assert others[0].delegation_scope == task.delegation_scope
    assert others[0].actor == task.actor
    assert captured.opens == ["src/main.py"]
