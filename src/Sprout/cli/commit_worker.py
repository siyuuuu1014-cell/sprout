"""Foreground, creator-bound commit worker assembly; never submit a workflow."""

from Sprout.cli.commit_submission import _route
from Sprout.cli.experiment_runtime import experiment_runtime as commit_runtime
from Sprout.orchestration.isolated_worker import COMMIT_ROUTING_PROFILE, IsolatedCommitWorker
from Sprout.orchestration.isolated_worker_lifecycle import run_owned_workers
from Sprout.orchestration.terminal.temporal import TemporalConfig
from Sprout.storage.local.sqlite.commit_status import _regular, commit_status_report


async def serve_commit_worker(database, task_id, request_id, *, settings, principal):
    database = _regular(database)
    report = commit_status_report(database, task_id, principal=principal,
                                  request_id=request_id, include_review=True)
    review = report["requests"][0]["review"]
    if review.get("routing_profile") != COMMIT_ROUTING_PROFILE:
        raise ValueError("Legacy request requires its original coding worker; no migration")
    route = _route(database, task_id, principal, COMMIT_ROUTING_PROFILE)
    async with commit_runtime(database, task_id, settings=settings) as (runtime, task):
        worker = IsolatedCommitWorker(runtime, task, TemporalConfig(
            host=route[0], namespace=route[1], task_queue=route[2]))
        await run_owned_workers(worker)


async def create_cli_commit_request(database, task_id, proposal_id, *, settings, principal,
                                    message, author_name=None, author_email=None,
                                    evaluation_node_id=None):
    """Create/reuse an exact pending review; never renew or approve an old request."""
    from Sprout.runtime.experiment_commits import ExperimentCommitService

    database = _regular(database)
    commit_status_report(database, task_id, principal=principal)
    route = _route(database, task_id, principal, COMMIT_ROUTING_PROFILE)
    async with commit_runtime(database, task_id, settings=settings) as (runtime, task):
        # Construction checks the same task/config/snapshot as the serving
        # path, but no worker.run, Temporal connection or model execution.
        IsolatedCommitWorker(runtime, task, TemporalConfig(
            host=route[0], namespace=route[1], task_queue=route[2]))
        result = await ExperimentCommitService(runtime).request(task_id, proposal_id,
            message=message, author_name=author_name, author_email=author_email,
            evaluation_node_id=evaluation_node_id)
        if "id" not in result:
            return result
        return {"request_id": result["id"], "task_id": task_id, "proposal_id": proposal_id,
                "decision_fingerprint": result["fingerprint"], "expires_at": result["expires_at"],
                "write_authorized": False, "automatic_retry": False,
                "next_action": "Use commit-review/status; identical requests are not renewed"}


async def refresh_cli_commit_request(database, task_id, request_id, *, settings, principal,
                                     expected_fingerprint):
    from Sprout.runtime.experiment_commits import ExperimentCommitService

    database = _regular(database)
    report = commit_status_report(database, task_id, principal=principal, request_id=request_id,
                                  include_review=True)
    if report["requests"][0]["decision_fingerprint"] != expected_fingerprint:
        raise ValueError("Predecessor fingerprint mismatch")
    route = _route(database, task_id, principal, COMMIT_ROUTING_PROFILE)
    async with commit_runtime(database, task_id, settings=settings) as (runtime, task):
        IsolatedCommitWorker(runtime, task, TemporalConfig(
            host=route[0], namespace=route[1], task_queue=route[2]))
        result = await ExperimentCommitService(runtime).refresh(request_id,
            expected_fingerprint=expected_fingerprint)
        if "id" not in result:
            return result
        return {"request_id": result["id"], "supersedes": result["supersedes"],
                "decision_fingerprint": result["fingerprint"], "expires_at": result["expires_at"],
                "decision_state": result["state"], "write_authorized": False,
                "next_action": "Review and explicitly decide the new request"}
