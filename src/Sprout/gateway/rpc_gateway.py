"""JSON-RPC gateway over the daemon project facade."""

from __future__ import annotations

import hashlib
from typing import Any

from Sprout.gateway.daemon_gateway import DaemonGateway
from Sprout.gateway.rpc_errors import RPCErrorCode
from Sprout.gateway.task_actions import TaskActions
from Sprout.gateway.task_queries import TaskQueries
from Sprout.runtime.runtime import Runtime
from Sprout.task.models import DelegationScope


class RPCGateway:
    """Routes JSON-RPC-style requests to project operations."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._daemon = DaemonGateway(runtime)

    async def handle(
        self, request: dict[str, Any], *, principal=None,
        delegation_scope: DelegationScope | None = None,
    ) -> dict[str, Any]:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        try:
            result = await self._dispatch(
                method, params, principal=principal, delegation_scope=delegation_scope)
        except PermissionError as exc:
            return self._error(request_id, RPCErrorCode.FORBIDDEN, str(exc))
        except KeyError as exc:
            return self._error(request_id, RPCErrorCode.INVALID_PARAMS, f"Missing {exc}")
        except LookupError as exc:
            return self._error(request_id, RPCErrorCode.NOT_FOUND, str(exc))
        except ValueError as exc:
            return self._error(request_id, RPCErrorCode.INVALID_STATE, str(exc))
        except Exception as exc:  # noqa: BLE001 - RPC must always return JSON
            return self._error(request_id, RPCErrorCode.INTERNAL_ERROR, str(exc))
        return {"id": request_id, "result": result}

    async def _dispatch(
        self, method: str, params: dict[str, Any], *, principal=None, delegation_scope=None,
    ):
        owned_proposal = None
        if method in {"proposal.show", "proposal.approve", "proposal.reject",
                      "proposal.apply", "proposal.rollback", "task.changes"}:
            if principal is None or not principal.authenticated:
                raise PermissionError("An authenticated adapter identity is required")
            if method == "task.changes":
                owner_task = params["task_id"]
            else:
                proposal = await self._runtime.find_change_proposal(params["proposal_id"])
                if proposal is None:
                    raise LookupError("Proposal not found")
                owned_proposal = proposal
                owner_task = proposal.task_id
            await TaskQueries(self._runtime)._owned_task(owner_task, principal)
        if method == "task.retry_submission":
            return await TaskActions(self._runtime).retry_submission(
                params["task_id"], principal=principal, submission_id=params["submission_id"])
        if method == "task.submit_persisted":
            return await TaskActions(self._runtime).submit(params["task_id"], principal=principal)
        if method == "task.sync":
            return await TaskActions(self._runtime).sync(
                params["task_id"], principal=principal,
                submission_id=params.get("submission_id"),
                wait_seconds=params.get("wait_seconds", 0),
            )
        if method == "task.report":
            return await TaskQueries(self._runtime).report(params["task_id"], principal=principal)
        if method == "task.observe":
            return await TaskQueries(self._runtime).observe(
                params["task_id"], principal=principal,
                submission_id=params.get("submission_id"),
                wait_seconds=params.get("wait_seconds", 0),
            )
        if method == "workspace.open":
            workspace = await self._daemon.open_workspace(params["path"])
            return {"id": workspace.id, "kind": workspace.kind.value}
        if method == "workspace.list":
            return [
                {"id": item.id, "kind": item.kind.value, "root": str(item.root)}
                for item in await self._daemon.list_workspaces()
            ]
        if method == "task.create":
            task = await self._daemon.create_task(
                params["workspace_id"],
                params["instruction"],
                execution_mode=params.get("execution_mode", "code"),
                principal=principal,
                delegation_scope=delegation_scope,
            )
            return {"id": task.id, "status": task.status.value}
        if method == "task.run":
            response = await self._daemon.run_task(params["task_id"], principal=principal)
            return {
                "task_id": response.task_id,
                "status": response.status,
            }
        if method == "task.submit":
            task = await TaskQueries(self._runtime)._owned_task(
                params["task_id"], principal)
            await self._runtime.submit_task(task)
            return {"task_id": task.id, "queued": True}
        if method == "task.changes":
            proposals = await self._daemon.list_changes(params["task_id"])
            return [
                {
                    "id": item.id,
                    "status": item.status.value,
                    "risk": item.risk,
                    "files_changed": list(item.files_changed),
                }
                for item in proposals
            ]
        if method == "proposal.show":
            proposal = owned_proposal
            if proposal is None:
                raise LookupError(f"Proposal not found: {params['proposal_id']}")
            metadata = proposal.metadata
            git = metadata.get("git_diff")
            if git is not None:
                # Public review evidence, not internal Worktree/control paths.
                git = {k: git[k] for k in (
                    "status", "source", "trust_level", "sha256", "task_id", "node_id",
                    "workspace_id", "sandbox_id", "baseline", "paths", "changed_paths",
                    "patches", "coverage", "budgets",
                ) if k in git}
                git["commands"] = [
                    {"exit_code": c["exit_code"],
                     "stdout_sha256": hashlib.sha256(c.get("stdout", "").encode()).hexdigest(),
                     "stderr_sha256": hashlib.sha256(c.get("stderr", "").encode()).hexdigest()}
                    for c in metadata["git_diff"].get("commands", ())
                ]
            content = metadata.get("content_diff")
            content_sha = (hashlib.sha256(content["text"].encode()).hexdigest()
                           if content is not None else None)
            return {
                "id": proposal.id,
                "task_id": proposal.task_id,
                "status": proposal.status.value,
                "risk": proposal.risk,
                "files_changed": list(proposal.files_changed),
                "diffs": [diff.diff_text for diff in proposal.diffs],
                **({"git_diff": git} if git is not None else {}),
                "content_diff": content,
                "content_diff_sha256": content_sha,
                "apply_plan_sha256": metadata.get("apply_plan_sha256"),
                "diff_format": {
                    "git_diff": metadata.get("diff_format") if git is not None else None,
                    "content_diff": content.get("format") if content is not None else None,
                },
            }
        if method == "proposal.approve":
            proposal = owned_proposal
            if proposal is None:
                raise LookupError(f"Proposal not found: {params['proposal_id']}")
            updated = await self._runtime.approve_change_proposal(
                proposal.id,
                decided_by="rpc",
                principal=principal,
            )
            return {"id": updated.id, "status": updated.status.value}
        if method == "proposal.reject":
            proposal = owned_proposal
            if proposal is None:
                raise LookupError(f"Proposal not found: {params['proposal_id']}")
            updated = await self._runtime.reject_change_proposal(
                proposal.id,
                reason=params.get("reason", ""),
            )
            return {"id": updated.id, "status": updated.status.value}
        if method == "proposal.apply":
            proposal = owned_proposal
            if proposal is None:
                raise LookupError(f"Proposal not found: {params['proposal_id']}")
            result = await self._runtime.apply_change_proposal(proposal.id, principal=principal)
            return {
                "proposal_id": result.proposal_id,
                "applied": result.applied,
                "reason": result.reason,
            }
        if method == "proposal.rollback":
            proposal = owned_proposal
            if proposal is None:
                raise LookupError(f"Proposal not found: {params['proposal_id']}")
            result = await self._runtime.rollback_change_proposal(proposal.id)
            return {
                "proposal_id": result.proposal_id,
                "applied": result.applied,
                "reason": result.reason,
            }
        if method == "trajectory.query":
            events = await self._runtime.read_trajectory(params["task_id"])
            return [
                {
                    "id": event.id,
                    "name": event.name,
                    "task_id": event.task_id,
                    "payload": dict(event.payload),
                }
                for event in events
            ]
        return self._error(None, RPCErrorCode.METHOD_NOT_FOUND, f"Unsupported method: {method}")

    @staticmethod
    def _error(request_id, code: RPCErrorCode, message: str) -> dict[str, Any]:
        return {
            "id": request_id,
            "error": {
                "code": code.value,
                "message": message,
            },
        }
