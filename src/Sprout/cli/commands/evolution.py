"""``sprout evolution``: inspect and drive the growth layer."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Growth layer status and operations.", no_args_is_help=True)


@app.command("commit-request")
def commit_request_cmd(
    task_id: Annotated[str, typer.Argument()],
    proposal_id: Annotated[str, typer.Argument()],
    database: Annotated[Path, typer.Option("--database")],
    message: Annotated[str, typer.Option("--message")],
    config: Annotated[Path | None, typer.Option("--config")] = None,
    author_name: Annotated[str | None, typer.Option("--author-name")] = None,
    author_email: Annotated[str | None, typer.Option("--author-email")] = None,
    evaluation_node_id: Annotated[str | None, typer.Option("--evaluation-node-id")] = None,
) -> None:
    """Review live evidence and save a commit request; no approval, dispatch or Git write."""
    from Sprout.cli.commit_worker import create_cli_commit_request
    from Sprout.config.loader import load_settings
    from Sprout.gateway.local_identity import current_cli_principal

    try:
        result = asyncio.run(create_cli_commit_request(database, task_id, proposal_id,
            settings=load_settings(config), principal=current_cli_principal(), message=message,
            author_name=author_name, author_email=author_email,
            evaluation_node_id=evaluation_node_id))
    except (ValueError, LookupError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("commit-refresh")
def commit_refresh_cmd(
    task_id: Annotated[str, typer.Argument()],
    request_id: Annotated[str, typer.Argument()],
    database: Annotated[Path, typer.Option("--database")],
    expected_fingerprint: Annotated[str, typer.Option("--expected-fingerprint")],
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Re-review an expired, never-dispatched request; require a new decision."""
    from Sprout.cli.commit_worker import refresh_cli_commit_request
    from Sprout.config.loader import load_settings
    from Sprout.gateway.local_identity import current_cli_principal

    try:
        result = asyncio.run(refresh_cli_commit_request(database, task_id, request_id,
            settings=load_settings(config), principal=current_cli_principal(),
            expected_fingerprint=expected_fingerprint))
    except (ValueError, LookupError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("commit-worker")
def commit_worker_cmd(
    task_id: Annotated[str, typer.Argument(help="Exact isolated task ID.")],
    request_id: Annotated[str, typer.Argument(help="Existing versioned commit request ID.")],
    database: Annotated[Path, typer.Option("--database", help="Existing experiment metadata.db.")],
    config: Annotated[Path | None, typer.Option("--config", help="Original settings file.")] = None,
) -> None:
    """Serve the dedicated commit queue in foreground; Ctrl+C stops this worker only."""
    from Sprout.cli.commit_worker import serve_commit_worker
    from Sprout.config.loader import load_settings
    from Sprout.gateway.local_identity import current_cli_principal

    try:
        asyncio.run(serve_commit_worker(database, task_id, request_id,
            settings=load_settings(config), principal=current_cli_principal()))
    except KeyboardInterrupt:
        typer.echo("Commit worker stopped; persisted workflows and ownership retained.")
    except (ValueError, LookupError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("commit-finalize")
def commit_finalize_cmd(
    task_id: Annotated[str, typer.Argument()],
    request_id: Annotated[str, typer.Argument()],
    database: Annotated[Path, typer.Option("--database")],
) -> None:
    """Finish task graph after an applied commit; no Runtime, scheduling or Git execution."""
    from Sprout.cli.commit_completion import finalize_cli_commit
    from Sprout.gateway.local_identity import current_cli_principal

    try:
        result = asyncio.run(finalize_cli_commit(database, task_id, request_id,
                                                principal=current_cli_principal()))
    except (ValueError, LookupError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("commit-submit")
def commit_submit_cmd(
    task_id: Annotated[str, typer.Argument(help="Exact isolated task ID.")],
    request_id: Annotated[str, typer.Argument(help="Approved commit inbox request ID.")],
    database: Annotated[Path, typer.Option("--database", help="Existing experiment metadata.db.")],
    expected_fingerprint: Annotated[str | None, typer.Option("--expected-fingerprint")] = None,
) -> None:
    """Relay an approved request to its original Temporal queue; no local execution."""
    from Sprout.cli.commit_submission import submit_cli_commit
    from Sprout.gateway.local_identity import current_cli_principal

    try:
        result = asyncio.run(submit_cli_commit(database, task_id, request_id,
            principal=current_cli_principal(), expected_fingerprint=expected_fingerprint))
    except (ValueError, LookupError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("commit-review")
def commit_review_cmd(
    task_id: Annotated[str, typer.Argument(help="Exact isolated task ID.")],
    request_id: Annotated[str, typer.Argument(help="Existing commit inbox request ID.")],
    database: Annotated[Path, typer.Option("--database", help="Existing experiment metadata.db.")],
) -> None:
    """Display the frozen inbox review with credential-pattern redaction; no writes."""
    from Sprout.gateway.local_identity import current_cli_principal
    from Sprout.storage.local.sqlite.commit_status import commit_status_report

    try:
        report = commit_status_report(database, task_id, principal=current_cli_principal(),
                                      request_id=request_id, include_review=True)
    except (ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@app.command("commit-decide")
def commit_decide_cmd(
    task_id: Annotated[str, typer.Argument(help="Exact isolated task ID.")],
    request_id: Annotated[str, typer.Argument(help="Existing commit inbox request ID.")],
    database: Annotated[Path, typer.Option("--database", help="Existing experiment metadata.db.")],
    decision: Annotated[str | None, typer.Option("--decision", help="approve or reject.")] = None,
    expected_fingerprint: Annotated[str | None, typer.Option("--expected-fingerprint")] = None,
    reason: Annotated[str, typer.Option("--reason")] = "",
) -> None:
    """Save an explicit operator decision; never dispatch or run Git locally."""
    from Sprout.cli.commit_decision import save_cli_decision
    from Sprout.gateway.local_identity import current_cli_principal
    from Sprout.storage.local.sqlite.commit_status import commit_status_report

    try:
        principal = current_cli_principal()
        report = commit_status_report(database, task_id, principal=principal,
                                      request_id=request_id, include_review=True)
        item = report["requests"][0]
        if decision is None and expected_fingerprint is None:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError("Non-interactive decisions require --decision and "
                                 "--expected-fingerprint from commit-review")
            typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
            decision = typer.prompt("Decision (approve/reject/cancel)", default="cancel")
            if decision == "cancel":
                typer.echo("Cancelled; no decision saved")
                return
            expected_fingerprint = item["decision_fingerprint"]
        if decision not in {"approve", "reject"} or expected_fingerprint is None:
            raise ValueError("Explicit approve/reject and reviewed fingerprint required")
        saved = asyncio.run(save_cli_decision(database, task_id, request_id, principal=principal,
            expected_fingerprint=expected_fingerprint,
            approved=decision == "approve", reason=reason))
        if not saved:
            raise ValueError("Commit request changed, expired or already decided; review again")
        typer.echo(json.dumps({"decision_saved": True, "request_id": request_id,
            "status": "waiting_schedule" if decision == "approve" else "rejected",
            "write_authorized": False, "local_execution": False}, ensure_ascii=False, indent=2))
    except (ValueError, LookupError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("commit-status")
def commit_status_cmd(
    task_id: Annotated[str, typer.Argument(help="Exact isolated task ID.")],
    database: Annotated[Path, typer.Option("--database", help="Existing experiment metadata.db.")],
    request_id: Annotated[str | None, typer.Option("--request-id")] = None,
) -> None:
    """List persisted commit inbox/results; never start Runtime, worker or containers."""
    from Sprout.gateway.local_identity import current_cli_principal
    from Sprout.storage.local.sqlite.commit_status import commit_status_report

    try:
        report = commit_status_report(database, task_id, principal=current_cli_principal(),
                                      request_id=request_id)
    except (ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@app.command("commit-identity")
def commit_identity_cmd(
    replay_plan: Annotated[Path, typer.Option("--replay-plan", help="Frozen replay JSON plan.")],
    git_plan: Annotated[Path, typer.Option("--git-plan", help="Read-only commit JSON plan.")],
    message: Annotated[str, typer.Option("--message", help="Explicit commit description.")],
    author_name: Annotated[str | None, typer.Option("--author-name")] = None,
    author_email: Annotated[str | None, typer.Option("--author-email")] = None,
    interactive: Annotated[bool, typer.Option("--interactive")] = False,
) -> None:
    """Preview task-scoped identity for approval; never commit, approve or change config."""
    from Sprout.execution.git_identity import GitIdentityRequired, resolve_commit_contract

    try:
        replay = json.loads(replay_plan.read_text(encoding="utf-8"))
        planned = json.loads(git_plan.read_text(encoding="utf-8"))
        while True:
            try:
                contract = asyncio.run(resolve_commit_contract(
                    replay, planned, message=message,
                    author_name=author_name, author_email=author_email))
                break
            except GitIdentityRequired as exc:
                if not interactive:
                    typer.echo(json.dumps({"status": "waiting_configuration",
                        "missing_fields": list(exc.missing), "automatic_retry": False,
                        "next_action": "Use --interactive or explicit --author-name/--author-email",
                        "write_authorized": False}, ensure_ascii=False, indent=2))
                    raise typer.Exit(code=2) from exc
                if "user.name" in exc.missing:
                    author_name = typer.prompt("Git author name (this experiment only)")
                if "user.email" in exc.missing:
                    author_email = typer.prompt("Git author email (this experiment only)")
        typer.echo(json.dumps({"status": "awaiting_exact_approval", "contract": contract,
            "write_authorized": False, "validation_committed": False,
            "configuration_changed": False}, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("comparison-report")
def comparison_report_cmd(
    pair: Annotated[Path, typer.Argument(help="Existing growth-comparison JSON manifest.")],
) -> None:
    """Recompute paired evidence and test outcomes without validating or publishing."""
    from Sprout.evolution.comparison_report import comparison_report

    try:
        output = comparison_report(pair)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2))


@app.command("replay-measure")
def replay_measure_cmd(
    task_id: Annotated[str, typer.Argument(help="Completed isolated coding task.")],
    database: Annotated[Path, typer.Option("--database", help="Existing experiment metadata.db.")],
    config: Annotated[str | None, typer.Option("--config")] = None,
    approve: Annotated[str | None, typer.Option("--approve", help="Exact pending process request.")]
    = None,
) -> None:
    """Independently measure a confirmed commit with Docker; no model, Git write or publish."""
    from Sprout.cli.completed_measurement import measure_completed
    from Sprout.config.loader import load_settings
    from Sprout.gateway.local_identity import current_cli_principal

    try:
        output = asyncio.run(measure_completed(database, task_id, settings=load_settings(config),
            principal=current_cli_principal(), approval_id=approve))
    except (ValueError, KeyError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2, default=str))


@app.command("coding-worker")
def coding_worker_cmd(
    task_id: Annotated[str, typer.Argument(help="Existing isolated coding task ID.")],
    database: Annotated[Path, typer.Option("--database")],
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Serve an accepted experiment queue until Ctrl+C; never submit/resume/approve."""
    from Sprout.cli.coding_worker import serve_coding_worker
    from Sprout.config.loader import load_settings
    from Sprout.gateway.local_identity import current_cli_principal

    try:
        asyncio.run(serve_coding_worker(database, task_id, settings=load_settings(config),
                                       principal=current_cli_principal()))
    except KeyboardInterrupt:
        typer.echo("coding worker stopped; task state retained")
    except (ValueError, LookupError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("coding-status")
def coding_status_cmd(
    task_id: Annotated[str, typer.Argument()],
    database: Annotated[Path, typer.Option("--database")],
    submission_id: Annotated[str | None, typer.Option("--submission-id")] = None,
    wait_seconds: Annotated[float, typer.Option("--wait-seconds")] = 0,
) -> None:
    """Observe an accepted workflow; never submit, start Worker, approve or write status."""
    from Sprout.cli.coding_observation import coding_status
    from Sprout.gateway.local_identity import current_cli_principal

    try:
        result = asyncio.run(coding_status(database, task_id, principal=current_cli_principal(),
            submission_id=submission_id, wait_seconds=wait_seconds))
    except (ValueError, KeyError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("coding-submit")
def coding_submit_cmd(
    task_id: Annotated[str, typer.Argument()],
    database: Annotated[Path, typer.Option("--database")],
    temporal_host: Annotated[str, typer.Option("--temporal-host")],
    temporal_namespace: Annotated[str, typer.Option("--temporal-namespace")],
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Submit one fresh persisted experiment; do not start Worker, wait or resume."""
    from Sprout.cli.coding_submission import submit_coding
    from Sprout.config.loader import load_settings
    from Sprout.gateway.local_identity import current_cli_principal

    try:
        result = asyncio.run(submit_coding(database, task_id, settings=load_settings(config),
            principal=current_cli_principal(), host=temporal_host, namespace=temporal_namespace))
    except (ValueError, KeyError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


@app.command("coding-sync")
def coding_sync_cmd(
    task_id: Annotated[str, typer.Argument()],
    database: Annotated[Path, typer.Option("--database")],
    submission_id: Annotated[str | None, typer.Option("--submission-id")] = None,
    wait_seconds: Annotated[float, typer.Option("--wait-seconds")] = 0,
) -> None:
    """Explicitly persist a trusted workflow result to metadata.db; never rerun or apply."""
    from Sprout.cli.coding_sync import sync_coding
    from Sprout.gateway.local_identity import current_cli_principal

    try:
        result = asyncio.run(sync_coding(database, task_id, principal=current_cli_principal(),
            submission_id=submission_id, wait_seconds=wait_seconds))
    except (ValueError, KeyError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("coding-report")
def coding_report_cmd(
    task_id: Annotated[str, typer.Argument()],
    database: Annotated[Path, typer.Option("--database")],
) -> None:
    """Read persisted stage, budget, verification and commit evidence; never execute."""
    from Sprout.gateway.local_identity import current_cli_principal
    from Sprout.storage.local.sqlite.coding_report import coding_report

    try:
        report = coding_report(database, task_id, principal=current_cli_principal())
    except (ValueError, KeyError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@app.command("comparison-refresh")
def comparison_refresh_cmd(pair: Annotated[Path, typer.Argument(help="Original pair manifest.")]):
    """Create a new pair using completed measurements; never overwrite old evidence or publish."""
    from Sprout.evolution.comparison_refresh import refresh_comparison

    try:
        output = refresh_comparison(pair)
    except (ValueError, KeyError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2, default=str))


@app.command("replay-resume")
def replay_resume_cmd(
    task_id: Annotated[str, typer.Argument(help="Exact isolated task ID, not source task.")],
    database: Annotated[Path, typer.Option("--database", help="Existing isolated metadata.db.")],
    config: Annotated[str | None, typer.Option("--config")] = None,
    approve: Annotated[str | None, typer.Option(
        "--approve", help="Explicitly approve this exact pending process request before recovery."
    )] = None,
) -> None:
    """Resume a pristine preflight wait once; may incur model cost. No force/retry option."""
    from Sprout.config.loader import load_settings
    from Sprout.evolution.replay_resume import resume_replay
    from Sprout.gateway.local_identity import current_cli_principal
    from Sprout.storage.local.sqlite.verification_report import recovery_facts

    settings = load_settings(config)
    principal = current_cli_principal()

    async def run():
        source = None
        try:
            facts = recovery_facts(database, task_id)
            from Sprout.evolution.replay_recovery import inspect_recovery

            checked = inspect_recovery(database, task_id, settings=settings, principal=principal)
            if checked["blockers"] != ["current_runtime_snapshot_required"]:
                return {"task_id": task_id, "status": "waiting_resource",
                        "reason": checked["blockers"], "validation_committed": False}
            plan = facts["metadata"].get("replay_plan", {}).get("payload", {})
            if plan.get("candidate"):
                from Sprout.runtime.factory import create_runtime

                source = create_runtime(settings)
            return await resume_replay(
                database, task_id, settings=settings, principal=principal,
                approval_id=approve, source_metadata=source.storage.metadata if source else None,
            )
        finally:
            if source is not None:
                await source.stop()

    try:
        output = asyncio.run(run())
    except (ValueError, LookupError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2, default=str))


@app.command("replay-continue")
def replay_continue_cmd(
    task_id: Annotated[str, typer.Argument(help="Existing isolated coding task, not source task.")],
    database: Annotated[Path, typer.Option("--database", help="Existing experiment metadata.db.")],
    approve: Annotated[str | None, typer.Option("--approve", help="Exact pending tool/process ID.")]
    = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """Continue tool/process approval waits on the original queue; may incur model cost."""
    from Sprout.cli.coding_continuation import continue_coding
    from Sprout.config.loader import load_settings
    from Sprout.gateway.local_identity import current_cli_principal
    from Sprout.runtime.factory import create_runtime

    settings = load_settings(config)

    async def run():
        source = create_runtime(settings)
        try:
            return await continue_coding(database, task_id, settings=settings,
                principal=current_cli_principal(), approval_id=approve,
                source_metadata=source.storage.metadata)
        finally:
            await source.storage.close()

    try:
        output = asyncio.run(run())
    except (ValueError, LookupError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2, default=str))


@app.command("replay-recovery-check")
def replay_recovery_check(
    task_id: Annotated[str, typer.Argument(help="Exact isolated task ID.")],
    database: Annotated[Path, typer.Option("--database", help="Existing isolated metadata DB.")],
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """Check preflight recovery evidence; never resume, approve, or launch a container."""
    from Sprout.config.loader import load_settings
    from Sprout.evolution.replay_recovery import inspect_recovery
    from Sprout.gateway.local_identity import current_cli_principal

    try:
        report = inspect_recovery(database, task_id, settings=load_settings(config),
                                  principal=current_cli_principal())
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@app.command("verification-report")
def verification_report_cmd(
    database: Annotated[Path, typer.Option("--database", help="Existing isolated metadata DB.")],
    execution_id: Annotated[str | None, typer.Option("--execution-id")] = None,
) -> None:
    """Inspect verification claims without Runtime, Docker, retry, or database writes."""
    from Sprout.storage.local.sqlite.verification_report import verification_report

    try:
        report = verification_report(database, execution_id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))


@app.command("replay-compare")
def replay_compare(
    artifact_id: Annotated[str, typer.Argument(help="Exact candidate artifact ID.")],
    task_id: Annotated[str, typer.Argument(help="Source task ID; never resumed.")],
    revision: Annotated[str, typer.Option("--revision", help="Explicit Git commit/ref.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Evidence directory outside source workspace.")
    ],
    check: Annotated[list[str], typer.Option("--check", help="Explicit test command; repeatable.")],
    regression: Annotated[
        list[str], typer.Option("--regression", help="Regression command; repeatable.")
    ],
    oracle: Annotated[
        list[str] | None,
        typer.Option("--oracle", help="Frozen repository-relative test/config file or directory."),
    ] = None,
    verification_image: Annotated[
        str | None,
        typer.Option("--verification-image", help="Pinned local Docker image ID or digest."),
    ] = None,
    regression_spec: Annotated[
        Path | None, typer.Option("--regression-spec", help="Explicit regression JSON oracle.")
    ] = None,
    coding_image: Annotated[
        str | None, typer.Option("--coding-image", help="Pinned isolated-coding Docker image.")
    ] = None,
    temporal_host: Annotated[
        str | None, typer.Option("--temporal-host", help="Explicit isolated Temporal endpoint.")
    ] = None,
    temporal_namespace: Annotated[
        str | None, typer.Option("--temporal-namespace", help="Existing experiment namespace.")
    ] = None,
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """Explicit independent baseline/candidate runs; never auto-approve or publish.

    Uses the configured model and may incur cost. Filesystem separation is not
    an OS sandbox. Execution and approvals retain existing Runtime policies.
    Code verification requires a commissioned Docker backend; no host fallback.
    """
    from Sprout.artifacts.models import ArtifactKind
    from Sprout.config.loader import load_settings
    from Sprout.evolution.isolated_replay import content_digest, git
    from Sprout.evolution.replay_runner import ReplayRunner
    from Sprout.gateway.local_identity import current_cli_principal
    from Sprout.runtime.factory import create_runtime
    from Sprout.trajectory.models import ArtifactSnapshot

    principal = current_cli_principal()
    from Sprout.evolution.regression_spec import load_operator_spec

    frozen_regressions = load_operator_spec(regression_spec) if regression_spec else None
    settings = load_settings(config)
    runtime = create_runtime(settings)

    async def run() -> None:
        try:
            artifact = await runtime.storage.metadata.get_artifact(artifact_id)
            if artifact is None:
                raise LookupError("Candidate artifact not found")
            if artifact.kind not in {ArtifactKind.SKILL, ArtifactKind.WORKFLOW}:
                raise ValueError("Replay supports only skill/workflow candidates")
            from Sprout.evolution.candidate_evaluator import CandidateEvaluator
            from Sprout.evolution.candidate_manager import CandidateManager

            report = CandidateEvaluator().evaluate(CandidateManager._to_candidate(artifact))
            if not report.passed and not report.pending:
                raise ValueError("Candidate failed static safety or utility gates")
            source = await runtime.get_task(task_id)
            if source is None:
                raise LookupError("Source task not found")
            workspace = await runtime.get_workspace(source.workspace_id)
            if workspace is None:
                raise LookupError("Source workspace not found")
            root, output = Path(workspace.root).resolve(), output_dir.resolve()
            if root.is_relative_to(output) or output.is_relative_to(root):
                raise ValueError("Replay output must not overlap the source workspace")
            if not revision or revision.startswith("-"):
                raise ValueError("Explicit valid revision required")
            # Resolve a moving ref once, before either experiment, so HEAD
            # changing between runs cannot silently change the baseline.
            commit = await asyncio.to_thread(
                git, workspace.root, "rev-parse", "--verify", revision + "^{commit}"
            )
            snapshot = ArtifactSnapshot(
                artifact.id, artifact.version, content_digest(artifact.content)
            )
            runner = ReplayRunner(runtime)
            kwargs = dict(
                revision=commit,
                verification_commands=tuple(check),
                regression_commands=tuple(regression),
                oracle_paths=tuple(oracle or ()),
                verification_image=verification_image,
                settings=settings,
                output_dir=output_dir,
                cli_principal=principal,
                regression_spec=frozen_regressions,
                coding_image=coding_image,
                temporal_host=temporal_host,
                temporal_namespace=temporal_namespace,
            )
            baseline = await runner.run(task_id, **kwargs)
            candidate = await runner.run(task_id, artifact_snapshot=snapshot, **kwargs)
            from Sprout.evolution.replay_pair import write_pair

            pair_path = write_pair(output_dir, baseline, candidate)
            typer.echo(
                json.dumps(
                    {
                        "baseline": asdict(baseline),
                        "candidate": asdict(candidate),
                        "validation_committed": False,
                        "pair_manifest": pair_path,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        finally:
            # This authority Runtime is never started; stop() intentionally
            # leaves unstarted storage alone, so close its handles explicitly.
            await runtime.storage.close()

    asyncio.run(run())


def _runtime(config: str | None):
    from Sprout.config.loader import load_settings
    from Sprout.runtime.factory import create_runtime

    return create_runtime(load_settings(config))


def _manager(config: str | None):
    from Sprout.config.loader import load_settings
    from Sprout.evolution import build_candidate_manager
    from Sprout.runtime.factory import create_runtime

    settings = load_settings(config)
    runtime = create_runtime(settings)
    if runtime.storage.metadata is None:
        typer.secho("MetadataStore is not configured.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    return build_candidate_manager(runtime, settings)


@app.command("trajectory-collect")
def trajectory_collect(
    workspace_id: Annotated[str, typer.Argument(help="Workspace id.")],
    config: Annotated[str | None, typer.Option("--config", help="Path to sprout.toml.")] = None,
) -> None:
    """Collect eligible Trajectory files into new growth candidates."""
    from Sprout.config.loader import load_settings
    from Sprout.evolution.trajectory_growth import TrajectoryGrowthService
    from Sprout.runtime.factory import create_runtime

    settings = load_settings(config)
    runtime = create_runtime(settings)
    if runtime.storage.metadata is None:
        typer.secho("MetadataStore is not configured.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    async def run() -> None:
        service = TrajectoryGrowthService(runtime.storage.metadata)
        candidates = await service.collect(
            workspace_id,
            Path(settings.storage.trajectory_dir),
        )
        if not candidates:
            typer.echo("No eligible trajectory candidates.")
            return
        for candidate in candidates:
            artifact = candidate.artifact
            name = artifact.name if artifact is not None else candidate.id[:8]
            typer.echo(f"candidate: {name} evidence={len(candidate.evidence_ids)}")

    asyncio.run(run())


@app.command("candidates")
def candidates(
    config: Annotated[str | None, typer.Option("--config", help="Path to sprout.toml.")] = None,
) -> None:
    """List new growth candidates."""
    manager = _manager(config)

    async def run() -> None:
        items = await manager.list_candidates()
        if not items:
            typer.echo("No growth candidates.")
            return
        for item in items:
            artifact = item.artifact
            if artifact is None:
                continue
            typer.echo(
                f"[{artifact.status.value:>7}] {artifact.id[:8]} "
                f"{artifact.kind.value}/{artifact.name} evidence={len(item.evidence_ids)}"
            )

    asyncio.run(run())


@app.command("candidate-validate")
def candidate_validate_cmd(
    artifact_id: Annotated[str, typer.Argument(help="Exact current candidate ID.")],
    pair: Annotated[Path, typer.Option("--pair", help="Existing paired measurement manifest.")],
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Recompute paired evidence and record validation; never approve or publish."""
    from Sprout.config.loader import load_settings
    from Sprout.evolution.validation_handoff import validate_comparison
    from Sprout.runtime.factory import create_runtime

    async def run():
        runtime = create_runtime(load_settings(config))
        try:
            return await validate_comparison(runtime.storage.metadata, artifact_id, pair)
        finally:
            await runtime.storage.close()

    try:
        result = asyncio.run(run())
    except (ValueError, LookupError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("candidate-report")
def candidate_report(
    artifact_id: Annotated[str, typer.Argument(help="Artifact id or unambiguous prefix.")],
    config: Annotated[str | None, typer.Option("--config", help="Path to sprout.toml.")] = None,
) -> None:
    """Read validation and publication evidence without publishing or retrying."""
    manager = _manager(config)

    async def run() -> None:
        typer.echo(
            json.dumps(
                await manager.get_candidate_report(artifact_id), ensure_ascii=False, indent=2
            )
        )

    asyncio.run(run())


@app.command("consolidate")
def consolidate(
    config: Annotated[str | None, typer.Option("--config", help="Path to sprout.toml.")] = None,
) -> None:
    """Deprecate older versions of the same growth artifact."""
    from Sprout.config.loader import load_settings
    from Sprout.evolution.consolidation import ArtifactConsolidator
    from Sprout.runtime.factory import create_runtime

    settings = load_settings(config)
    runtime = create_runtime(settings)
    if runtime.storage.metadata is None:
        typer.secho("MetadataStore is not configured.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    async def run() -> None:
        changes = await ArtifactConsolidator().consolidate(runtime.storage.metadata)
        if not changes:
            typer.echo("No artifacts consolidated.")
            return
        for artifact_id, status in changes.items():
            typer.echo(f"{artifact_id[:8]} -> {status}")

    asyncio.run(run())


@app.command("maintain")
def maintain(
    config: Annotated[str | None, typer.Option("--config", help="Path to sprout.toml.")] = None,
) -> None:
    """Advance old MONITORING/STABLE artifacts."""
    from Sprout.config.loader import load_settings
    from Sprout.evolution.maintenance import ArtifactMaintenance
    from Sprout.runtime.factory import create_runtime

    settings = load_settings(config)
    runtime = create_runtime(settings)
    if runtime.storage.metadata is None:
        typer.secho("MetadataStore is not configured.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    async def run() -> None:
        changes = await ArtifactMaintenance().maintain(runtime.storage.metadata)
        if not changes:
            typer.echo("No artifacts changed.")
            return
        for artifact_id, status in changes.items():
            typer.echo(f"{artifact_id[:8]} -> {status}")

    asyncio.run(run())


@app.command("run-once")
def run_once(
    workspace_id: Annotated[str, typer.Argument(help="Workspace id.")],
    config: Annotated[str | None, typer.Option("--config", help="Path to sprout.toml.")] = None,
) -> None:
    """Run one trajectory collection and candidate evaluation pass."""
    from Sprout.config.loader import load_settings
    from Sprout.evolution.automation import GrowthAutomation
    from Sprout.runtime.factory import create_runtime

    settings = load_settings(config)
    runtime = create_runtime(settings)
    if runtime.storage.metadata is None:
        typer.secho("MetadataStore is not configured.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    async def run() -> None:
        automation = GrowthAutomation(
            runtime.storage.metadata,
            workspace_id,
            settings.storage.trajectory_dir,
            runtime=runtime,
        )
        result = await automation.run_once()
        typer.echo(
            f"candidates_collected={result['candidates_collected']} "
            f"status_changes={len(result['status_changes'])} "
            f"consolidated={len(result['consolidated'])} "
            f"maintained={len(result['maintained'])}"
        )

    asyncio.run(run())


@app.command("unified-run")
def unified_run(
    workspace_id: Annotated[str, typer.Argument(help="Workspace id.")],
    config: Annotated[str | None, typer.Option("--config", help="Path to sprout.toml.")] = None,
) -> None:
    """Run the single trajectory-driven growth pipeline (collect + evaluate)."""
    from Sprout.config.loader import load_settings
    from Sprout.evolution.unified import UnifiedGrowthService
    from Sprout.runtime.factory import create_runtime

    settings = load_settings(config)
    runtime = create_runtime(settings)
    if runtime.storage.metadata is None:
        typer.secho("MetadataStore is not configured.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    async def run() -> None:
        service = UnifiedGrowthService(
            runtime.storage.metadata,
            runtime.storage.observations,
        )
        trajectory_candidates = await service.collect_trajectories(
            workspace_id,
            settings.storage.trajectory_dir,
        )
        changes = await service.evaluate_all()
        typer.echo(
            f"trajectory_candidates={len(trajectory_candidates)} status_changes={len(changes)}"
        )

    asyncio.run(run())


@app.command("schedule")
def schedule(
    workspace_id: Annotated[str, typer.Argument(help="Workspace id.")],
    interval: Annotated[float, typer.Option("--interval", help="Run interval in seconds.")] = 300.0,
    config: Annotated[str | None, typer.Option("--config", help="Path to sprout.toml.")] = None,
) -> None:
    """Schedule periodic trajectory collection and candidate evaluation."""
    from Sprout.config.loader import load_settings
    from Sprout.evolution.automation import GrowthAutomation
    from Sprout.runtime.factory import create_runtime
    from Sprout.scheduler.scheduler import Scheduler

    settings = load_settings(config)
    runtime = create_runtime(settings)
    if runtime.storage.metadata is None:
        typer.secho("MetadataStore is not configured.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    async def run() -> None:
        automation = GrowthAutomation(
            runtime.storage.metadata,
            workspace_id,
            settings.storage.trajectory_dir,
            runtime=runtime,
        )
        scheduler = Scheduler()
        automation.attach(scheduler, interval_seconds=interval)
        scheduler.start()
        typer.echo(f"Scheduled growth automation every {interval} seconds. Ctrl+C to stop.")
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            await scheduler.stop()

    asyncio.run(run())


@app.command("evaluate-candidates")
def evaluate_candidates(
    config: Annotated[str | None, typer.Option("--config", help="Path to sprout.toml.")] = None,
) -> None:
    """Run hard gates and utility scoring on candidates."""
    manager = _manager(config)

    async def run() -> None:
        changes = await manager.evaluate_all()
        if not changes:
            typer.echo("No candidates changed.")
            return
        for artifact_id, status in changes.items():
            typer.echo(f"{artifact_id[:8]} -> {status}")

    asyncio.run(run())


@app.command("approve-candidate")
def approve_candidate(
    artifact_id: Annotated[str, typer.Argument(help="Artifact id or prefix.")],
    by: Annotated[str, typer.Option("--by", help="Approver identity.")] = "cli",
    config: Annotated[str | None, typer.Option("--config", help="Path to sprout.toml.")] = None,
) -> None:
    """Approve a validated growth candidate."""
    manager = _manager(config)

    async def run() -> None:
        artifact = await manager.approve(artifact_id, decided_by=by)
        typer.echo(f"published: {artifact.kind.value}/{artifact.name} v{artifact.version}")

    asyncio.run(run())


@app.command("reject-candidate")
def reject_candidate(
    artifact_id: Annotated[str, typer.Argument(help="Artifact id or prefix.")],
    reason: Annotated[str, typer.Option("--reason", help="Rejection reason.")] = "",
    config: Annotated[str | None, typer.Option("--config", help="Path to sprout.toml.")] = None,
) -> None:
    """Reject a validated growth candidate."""
    manager = _manager(config)

    async def run() -> None:
        artifact = await manager.reject(artifact_id, reason=reason)
        typer.echo(f"rejected: {artifact.kind.value}/{artifact.name}")

    asyncio.run(run())
