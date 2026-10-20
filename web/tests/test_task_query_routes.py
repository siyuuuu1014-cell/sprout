"""HTTP adapters reuse the owner-bound query service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from starlette.applications import Starlette
from starlette.testclient import TestClient

from Sprout.gateway.identity import Principal
from Sprout.task.models import Task, TaskStatus
from web.webapi.auth import WebAuth, WebAuthMiddleware
from web.webapi.routes.project import create_project_routes
from web.webapi.routes.rpc import create_rpc_routes


def fixture():
    task = Task(actor=Principal(user_id="web-user", display_name="web-user",
                                authenticated=True, source="web"))
    record = {"id": "submission", "task_id": task.id, "run_id": "run",
              "host": "unused", "namespace": "default", "task_queue": "queue",
              "accepted": True, "state": "resolved", "result": {"status": "completed"}}
    runtime = SimpleNamespace(
        get_task=AsyncMock(return_value=task),
        get_task_execution_report=AsyncMock(return_value={"nodes": []}),
        get_verification_report=AsyncMock(return_value={"verified": False}),
        get_task_budget_report=AsyncMock(return_value={"mode": "legacy_unavailable"}),
        get_task_submissions=AsyncMock(return_value=[record]),
        create_task_planned=AsyncMock(return_value=task), execute=AsyncMock(),
        submit_persisted_task=AsyncMock(return_value={"new_submission": True,
                                                    "worker_started": False}),
        retry_pending_submission=AsyncMock(return_value={"retry_requested": True,
                                                        "new_submission": False}),
    )
    app = Starlette(routes=create_project_routes(runtime) + create_rpc_routes(runtime))
    app.add_middleware(WebAuthMiddleware, auth=WebAuth(enabled=True, token="test-token"))
    return TestClient(app), runtime, task


def test_http_and_rpc_share_reports_and_only_observe():
    client, runtime, task = fixture()
    headers = {"Authorization": "Bearer test-token"}
    with client:
        created = client.post("/api/project/tasks", headers=headers, json={
            "workspace_id": "workspace", "instruction": "demo", "user_id": "forged"})
        assert created.status_code == 200
        assert runtime.create_task_planned.call_args.kwargs["actor"] == task.actor
        rpc_created = client.post("/api/rpc", headers=headers, json={
            "id": 0, "method": "task.create", "params": {
                "workspace_id": "workspace", "instruction": "demo", "user_id": "forged"}})
        assert "result" in rpc_created.json()
        assert runtime.create_task_planned.call_args.kwargs["actor"] == task.actor
        report = client.get(f"/api/project/tasks/{task.id}/report", headers=headers)
        rpc = client.post("/api/rpc", headers=headers, json={
            "id": 1, "method": "task.report", "params": {"task_id": task.id}})
        assert report.status_code == 200
        assert rpc.json()["result"] == report.json()
        observed = client.get(f"/api/project/tasks/{task.id}/observe", headers=headers)
        assert observed.json()["observation"] == "persisted_result"
        assert observed.json()["persisted_task_status"] == "created"
        rpc_observed = client.post("/api/rpc", headers=headers, json={
            "id": 2, "method": "task.observe", "params": {"task_id": task.id}})
        assert rpc_observed.json()["result"] == observed.json()
        submitted = client.post(f"/api/project/tasks/{task.id}/submit", headers=headers)
        rpc_submitted = client.post("/api/rpc", headers=headers, json={
            "id": 3, "method": "task.submit_persisted", "params": {"task_id": task.id}})
        assert submitted.json() == rpc_submitted.json()["result"]
        retried = client.post(f"/api/project/tasks/{task.id}/retry-submission", headers=headers,
                              json={"submission_id": "submission"})
        rpc_retried = client.post("/api/rpc", headers=headers, json={
            "id": 4, "method": "task.retry_submission", "params": {
                "task_id": task.id, "submission_id": "submission"}})
        assert retried.json() == rpc_retried.json()["result"]
    runtime.execute.assert_not_called()


def test_foreign_identity_parameters_and_invalid_observation():
    client, runtime, task = fixture()
    headers = {"Authorization": "Bearer test-token", "X-Sprout-User": "other"}
    with client:
        response = client.get(f"/api/project/tasks/{task.id}/report", headers=headers)
        assert response.status_code == 400  # Legacy identity-selection header refused.
        rpc = client.post("/api/rpc", headers=headers, json={
            "id": 1, "method": "task.report", "params": {
                "task_id": task.id, "principal": {"user_id": "web-user"}}})
        assert rpc.status_code == 400
        runtime.get_task_submissions.assert_not_called()
        headers.pop("X-Sprout-User")
        assert client.get(f"/api/project/tasks/{task.id}/observe?wait_seconds=nan",
                          headers=headers).status_code == 400
        runtime.get_task.return_value = None
        response = client.get(f"/api/project/tasks/{task.id}/report", headers=headers)
        assert response.status_code == 404


def test_anonymous_new_query_is_rejected():
    client, runtime, task = fixture()
    app = Starlette(routes=create_project_routes(runtime) + create_rpc_routes(runtime))
    with TestClient(app) as anonymous:
        assert anonymous.get(f"/api/project/tasks/{task.id}/report").status_code == 403
        result = anonymous.post("/api/rpc", json={"id": 1, "method": "task.report",
                                                 "params": {"task_id": task.id}})
        assert result.json()["error"]["code"] == "FORBIDDEN"
    runtime.get_task.assert_not_called()


def test_real_runtime_created_task_is_queryable_across_http_adapters(tmp_path):
    from Sprout.config.loader import default_settings
    from Sprout.runtime.factory import create_runtime

    settings = default_settings()
    settings.model.provider = "echo"
    settings.model.model = "echo-1"
    settings.storage.operational = "memory://"
    settings.storage.knowledge = "memory://"
    settings.storage.observations.enabled = False
    settings.storage.metadata = f"sqlite:///{tmp_path / 'metadata.db'}"
    settings.storage.trajectory_dir = str(tmp_path / "trajectory")
    settings.storage.blobs_dir = str(tmp_path / "blobs")
    settings.security.audit.path = str(tmp_path / "audit.jsonl")
    runtime = create_runtime(settings)
    app = Starlette(routes=create_project_routes(runtime) + create_rpc_routes(runtime))
    app.add_middleware(WebAuthMiddleware, auth=WebAuth(enabled=True, token="test-token"))
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        workspace = client.post("/api/project/workspaces", headers=headers,
                                json={"path": str(tmp_path)}).json()
        response = client.post("/api/project/tasks", headers=headers, json={
            "workspace_id": workspace["id"], "instruction": "explain",
            "execution_mode": "read_only"})
        assert response.status_code == 200
        task_id = response.json()["id"]
        report = client.get(f"/api/project/tasks/{task_id}/report", headers=headers)
        assert report.status_code == 200
        assert report.json()["budget"]["mode"] == "no_explicit_limit"
        rpc = client.post("/api/rpc", headers=headers, json={
            "id": 1, "method": "task.report", "params": {"task_id": task_id}})
        assert rpc.json()["result"] == report.json()
        metadata = runtime.storage.metadata
        client.portal.call(metadata.update_task_status, task_id, TaskStatus.RUNNING)
        record = {"id": "saved-result", "task_id": task_id, "state": "resolved",
                  "accepted": True, "run_id": "run", "host": "unused", "namespace": "ns",
                  "task_queue": "queue", "payload": {"nodes": []},
                  "result": {"status": "waiting_approval", "error": None, "node_id": None}}
        client.portal.call(metadata.ensure_task_submission, record)
        synced = client.post(f"/api/project/tasks/{task_id}/sync", headers=headers,
                             json={"result": {"status": "completed"}})
        assert synced.status_code == 200
        assert synced.json()["task_status"] == "waiting_approval"
        assert synced.json()["workflow_submitted"] is False
        repeated = client.post("/api/rpc", headers=headers, json={
            "id": 2, "method": "task.sync", "params": {"task_id": task_id}})
        assert repeated.json()["result"]["task_status_updated"] is False
        assert client.portal.call(metadata.list_execution_nodes, task_id) == []
        client.portal.call(runtime.stop)
