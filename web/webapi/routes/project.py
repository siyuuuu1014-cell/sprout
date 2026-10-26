"""Project Runtime HTTP routes."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from Sprout.gateway.project_gateway import ProjectGateway
from Sprout.gateway.task_actions import TaskActions
from Sprout.gateway.task_queries import TaskQueries
from Sprout.runtime.runtime import Runtime

from ..auth import principal_of


def _task_payload(task) -> dict:
    return {
        "id": task.id,
        "workspace_id": task.workspace_id,
        "instruction": task.instruction,
        "source": task.source,
        "status": task.status.value,
        "phases": [phase.value for phase in task.phases],
        "metadata": dict(task.metadata),
        "created_at": task.created_at.isoformat(),
    }


def create_project_routes(runtime: Runtime, *, analysis_task_provider=None) -> list[Route]:
    gateway = ProjectGateway(runtime, transport="web", default_user="web-user")

    async def list_workspaces(request: Request) -> JSONResponse:
        workspaces = await gateway.list_workspaces()
        return JSONResponse(
            [
                {
                    "id": item.id,
                    "kind": item.kind.value,
                    "root": str(item.root),
                }
                for item in workspaces
            ]
        )

    async def open_workspace(request: Request) -> JSONResponse:
        data = await request.json()
        path = data.get("path") if isinstance(data, dict) else None
        if not isinstance(path, str):
            return JSONResponse({"error": "path is required"}, status_code=400)
        workspace = await gateway.open_workspace(path)
        return JSONResponse(
            {
                "id": workspace.id,
                "kind": workspace.kind.value,
                "root": str(workspace.root),
            }
        )

    async def create_task(request: Request) -> JSONResponse:
        data = await request.json()
        if not isinstance(data, dict):
            return JSONResponse({"error": "expected object"}, status_code=400)
        try:
            task = await gateway.create_task(
                data["workspace_id"],
                data["instruction"],
                execution_mode=data.get("execution_mode", "code"),
                principal=principal_of(request),
            )
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"id": task.id, "status": task.status.value})

    async def submit_persisted_task(request: Request) -> JSONResponse:
        try:
            report = await TaskActions(runtime).submit(
                request.path_params["task_id"], principal=principal_of(request))
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(report)

    async def retry_submission(request: Request) -> JSONResponse:
        try:
            data = await request.json()
            if not isinstance(data, dict) or not isinstance(data.get("submission_id"), str):
                raise ValueError("submission_id is required")
            report = await TaskActions(runtime).retry_submission(
                request.path_params["task_id"], principal=principal_of(request),
                submission_id=data["submission_id"])
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(report)

    async def sync_task(request: Request) -> JSONResponse:
        try:
            data = await request.json() if await request.body() else {}
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            report = await TaskActions(runtime).sync(
                request.path_params["task_id"], principal=principal_of(request),
                submission_id=data.get("submission_id"), wait_seconds=data.get("wait_seconds", 0),
            )
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(report)

    async def task_query(request: Request) -> JSONResponse:
        queries = TaskQueries(runtime)
        try:
            if request.url.path.endswith("/observe"):
                report = await queries.observe(
                    request.path_params["task_id"], principal=principal_of(request),
                    submission_id=request.query_params.get("submission_id"),
                    wait_seconds=float(request.query_params.get("wait_seconds", "0")),
                )
            else:
                report = await queries.report(
                    request.path_params["task_id"], principal=principal_of(request),
                )
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(report)

    async def list_tasks(request: Request) -> JSONResponse:
        workspace_id = request.query_params.get("workspace_id")
        tasks = await runtime.list_tasks(workspace_id)
        return JSONResponse({"tasks": [_task_payload(task) for task in tasks]})

    async def analyze_workspace(request: Request) -> JSONResponse:
        # Optional Python host dependency, never recovered from HTTP JSON/state
        # supplied by the client. Authentication alone grants no resource Scope.
        principal = principal_of(request)
        workspace_id = request.path_params["workspace_id"]
        try:
            if (principal is None or principal.authenticated is not True
                    or analysis_task_provider is None):
                raise PermissionError("authorized_workspace_analysis_required")
            task = await analysis_task_provider(
                principal=principal, workspace_id=workspace_id)
            analysis = await gateway.analyze_workspace(
                workspace_id, principal=principal, analysis_task=task)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        return JSONResponse(
            {
                "workspace_id": request.path_params["workspace_id"],
                "nodes": len(analysis.graph.nodes),
                "edges": len(analysis.graph.edges),
                "knowledge": len(analysis.knowledge.items),
            }
        )

    async def run_task(request: Request) -> JSONResponse:
        task_id = request.path_params["task_id"]
        result = await gateway.run_task(task_id)
        return JSONResponse(
            {
                "task_id": result.task_id,
                "status": result.status,
            }
        )

    async def list_changes(request: Request) -> JSONResponse:
        task_id = request.path_params["task_id"]
        proposals = await gateway.list_changes(task_id)
        return JSONResponse(
            [
                {
                    "id": item.id,
                    "status": item.status.value,
                    "risk": item.risk,
                    "files_changed": list(item.files_changed),
                }
                for item in proposals
            ]
        )

    async def verification_report(request: Request) -> JSONResponse:
        try:
            report = await gateway.verification_report(request.path_params["task_id"])
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(report)

    async def show_proposal(request: Request) -> JSONResponse:
        proposal = await gateway.show_proposal(request.path_params["proposal_id"])
        return JSONResponse(
            {
                "id": proposal.id,
                "task_id": proposal.task_id,
                "status": proposal.status.value,
                "risk": proposal.risk,
                "files_changed": list(proposal.files_changed),
                "diffs": [diff.diff_text for diff in proposal.diffs],
            }
        )

    async def approve_proposal(request: Request) -> JSONResponse:
        proposal = await gateway.find_proposal(request.path_params["proposal_id"])
        updated = await gateway.approve_proposal(proposal.id)
        return JSONResponse({"id": updated.id, "status": updated.status.value})

    async def reject_proposal(request: Request) -> JSONResponse:
        proposal = await gateway.find_proposal(request.path_params["proposal_id"])
        data = await request.json() if await request.body() else {}
        reason = data.get("reason", "") if isinstance(data, dict) else ""
        updated = await gateway.reject_proposal(proposal.id, reason=reason)
        return JSONResponse({"id": updated.id, "status": updated.status.value})

    async def apply_proposal(request: Request) -> JSONResponse:
        proposal = await gateway.find_proposal(request.path_params["proposal_id"])
        result = await gateway.apply_proposal(proposal.id)
        return JSONResponse(
            {
                "proposal_id": result.proposal_id,
                "applied": result.applied,
                "reason": result.reason,
            }
        )

    return [
        Route("/api/project/workspaces", list_workspaces, methods=["GET"]),
        Route("/api/project/workspaces", open_workspace, methods=["POST"]),
        Route(
            "/api/project/workspaces/{workspace_id}/analyze",
            analyze_workspace,
            methods=["POST"],
        ),
        Route("/api/project/tasks", list_tasks, methods=["GET"]),
        Route("/api/project/tasks", create_task, methods=["POST"]),
        Route("/api/project/tasks/{task_id}/run", run_task, methods=["POST"]),
        Route("/api/project/tasks/{task_id}/changes", list_changes, methods=["GET"]),
        Route("/api/project/tasks/{task_id}/verification", verification_report, methods=["GET"]),
        Route("/api/project/tasks/{task_id}/report", task_query, methods=["GET"]),
        Route("/api/project/tasks/{task_id}/observe", task_query, methods=["GET"]),
        Route("/api/project/tasks/{task_id}/sync", sync_task, methods=["POST"]),
        Route("/api/project/tasks/{task_id}/submit", submit_persisted_task, methods=["POST"]),
        Route("/api/project/tasks/{task_id}/retry-submission", retry_submission, methods=["POST"]),
        Route("/api/project/proposals/{proposal_id}", show_proposal, methods=["GET"]),
        Route("/api/project/proposals/{proposal_id}/approve", approve_proposal, methods=["POST"]),
        Route("/api/project/proposals/{proposal_id}/reject", reject_proposal, methods=["POST"]),
        Route("/api/project/proposals/{proposal_id}/apply", apply_proposal, methods=["POST"]),
    ]
