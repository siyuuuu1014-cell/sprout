"""Explicit post-review closure of one successful live experiment; no model calls.

Uses the existing review/decision/submission/worker services. Never a local Git
fallback or direct database status edit. Run only with operator approval.
"""

import argparse
import asyncio
import json
from dataclasses import replace
from pathlib import Path

from temporalio.client import Client

from Sprout.cli.commit_submission import _route, submit_cli_commit
from Sprout.cli.experiment_runtime import experiment_runtime
from Sprout.config.loader import load_env_file, load_settings
from Sprout.gateway.local_identity import current_cli_principal
from Sprout.orchestration.isolated_worker import COMMIT_ROUTING_PROFILE, IsolatedCommitWorker
from Sprout.orchestration.terminal.temporal import TemporalConfig
from Sprout.runtime.experiment_commits import ExperimentCommitService
from Sprout.storage.local.sqlite.commit_status import commit_status_report
from Sprout.tests.acceptance_samples import check_sample_task


async def complete(report_path, config, output):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report.get("passed") is not True or report.get("evaluation_verified") is not True
            or report.get("original_unchanged") is not True
            or len(report.get("proposals", [])) != 1):
        raise ValueError("One verified live experiment proposal required")
    database = Path(report["evidence_dir"]).resolve() / "metadata.db"
    if not database.is_relative_to(report_path.parent.resolve()):
        raise ValueError("Report and database must belong to the same experiment")
    task_id, proposal_id = report["task_id"], report["proposals"][0]["id"]
    principal = current_cli_principal()
    host, namespace, queue = _route(database, task_id, principal, COMMIT_ROUTING_PROFILE)
    if host != "127.0.0.1:17233":
        raise ValueError("Dedicated trial Temporal route required")
    load_env_file()
    settings = load_settings(config)
    settings.model = replace(settings.model, max_output_tokens=8192)
    message = ("Repair cart pricing discount integration and add tests"
               if report.get("sample") == "cart" else
               "Fix integer addition and add tested positive negative zero cases")
    async with experiment_runtime(database, task_id, settings=settings) as (runtime, task):
        sample = check_sample_task(report.get("sample", "addition"), task)
        # Match commit-request CLI admission without starting a worker here.
        IsolatedCommitWorker(runtime, task, TemporalConfig(
            host=host, namespace=namespace, task_queue=queue))
        service = ExperimentCommitService(runtime)
        review = await service.review(task_id, proposal_id, message=message)
        if (review.get("status") != "awaiting_review"
                or sorted(review["files_changed"]) != sorted(sample["required_paths"])
                or review["proposal_status"] != "pending"):
            raise ValueError("Exact reviewed pending addition proposal required")
        # Normal review service, without Runtime's automatic task-resume wrapper.
        await runtime._changes().approve(proposal_id, decided_by=principal.user_id)
        request = await service.request(task_id, proposal_id, message=message)
        if "id" not in request:
            raise ValueError("Commit request did not become reviewable")
        await service.decide(request["id"], expected_fingerprint=request["fingerprint"],
            approved=True, reason="Operator authorized reviewed isolated trial commit")
        request_id, fingerprint = request["id"], request["fingerprint"]
    async with experiment_runtime(database, task_id, settings=settings) as (runtime, task):
        worker = IsolatedCommitWorker(runtime, task, TemporalConfig(
            host=host, namespace=namespace, task_queue=queue))
        serving = asyncio.create_task(worker.run())
        try:
            submitted = await submit_cli_commit(database, task_id, request_id,
                principal=principal, expected_fingerprint=fingerprint)
            client = await Client.connect(host, namespace=namespace)
            result = await asyncio.wait_for(client.get_workflow_handle(
                submitted["workflow_id"]).result(), timeout=60)
        finally:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
    status = commit_status_report(database, task_id, principal=principal, request_id=request_id)
    summary = {"task_id": task_id, "request_id": request_id, "result": result,
               "status": status, "model_requests": 0, "published": False}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2)
    print(json.dumps({"task_id": task_id, "request_id": request_id,
                      "result": result, "model_requests": 0, "published": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        raise SystemExit("Fresh output in an existing experiment directory required")
    asyncio.run(complete(args.report, args.config, args.output))
