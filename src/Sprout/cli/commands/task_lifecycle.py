"""Thin ordinary-task CLI over shared Gateway queries/actions."""

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Persisted ordinary task lifecycle.", no_args_is_help=True)
Config = Annotated[Path, typer.Option("--config", exists=True, dir_okay=False)]


async def operate(operation, config, *, task_id=None, route=None, **kwargs):
    from Sprout.config.loader import load_settings
    from Sprout.gateway.local_identity import current_cli_principal
    from Sprout.gateway.runtime_gateway import RuntimeGateway
    from Sprout.orchestration.terminal.temporal import TemporalOrchestratorBackend
    from Sprout.runtime.factory import create_runtime

    principal = current_cli_principal()
    runtime = create_runtime(load_settings(config))
    try:
        gateway = RuntimeGateway(runtime, transport="cli")
        if operation == "create":
            task = await runtime.create_task_planned(
                kwargs["workspace_id"], kwargs["instruction"], actor=principal,
                source="cli", execution_mode=kwargs["mode"])
            return {"task_id": task.id, "status": task.status.value}
        if operation == "submit":
            runtime._orchestrator_backend = TemporalOrchestratorBackend(
                route, metadata=runtime.storage.metadata)
            return await gateway.actions.submit(task_id, principal=principal)
        service = gateway.actions if operation in {"sync", "retry_submission"} else gateway.queries
        return await getattr(service, operation)(task_id, principal=principal, **kwargs)
    finally:
        # No Runtime.start: no housekeeping, background queue or worker ownership.
        await runtime.storage.close()


def invoke(operation, config, **kwargs):
    try:
        result = asyncio.run(operate(operation, config, **kwargs))
    except (ValueError, LookupError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(json.dumps(result, ensure_ascii=False))


@app.command("create")
def create(workspace_id: str, instruction: str, config: Config,
           mode: Annotated[str, typer.Option("--mode", help="code or read_only")] = "code"):
    """Create without executing; bind the current verified OS identity."""
    invoke("create", config, workspace_id=workspace_id, instruction=instruction, mode=mode)


@app.command("submit")
def submit(task_id: str, config: Config,
           host: Annotated[str, typer.Option("--host")],
           namespace: Annotated[str, typer.Option("--namespace")],
           task_queue: Annotated[str, typer.Option("--task-queue")]):
    """Submit once to explicit Temporal route; never start a worker or retry history."""
    from Sprout.orchestration.terminal.temporal import TemporalConfig

    if any(not value.strip() for value in (host, namespace, task_queue)):
        raise typer.BadParameter("Nonempty Temporal route required")
    invoke("submit", config, task_id=task_id,
           route=TemporalConfig(host=host, namespace=namespace, task_queue=task_queue))


@app.command("report")
def report(task_id: str, config: Config):
    """Inspect task evidence, without running checks or contacting Temporal."""
    invoke("report", config, task_id=task_id)


@app.command("retry-submission")
def retry_submission(task_id: str, config: Config,
                     submission_id: Annotated[str, typer.Option("--submission-id")]):
    """Explicit retry of known connection failure; preserve original ID/payload/route."""
    invoke("retry_submission", config, task_id=task_id, submission_id=submission_id)


@app.command("observe")
def observe(task_id: str, config: Config,
            submission_id: Annotated[str | None, typer.Option("--submission-id")] = None,
            wait_seconds: Annotated[float, typer.Option("--wait-seconds", min=0, max=60)] = 0):
    """Observe original workflow; do not synchronize or cancel."""
    invoke("observe", config, task_id=task_id,
           submission_id=submission_id, wait_seconds=wait_seconds)


@app.command("sync")
def sync(task_id: str, config: Config,
         submission_id: Annotated[str | None, typer.Option("--submission-id")] = None,
         wait_seconds: Annotated[float, typer.Option("--wait-seconds", min=0, max=60)] = 0):
    """Explicitly synchronize trusted workflow result; never submit or apply code."""
    invoke("sync", config, task_id=task_id,
           submission_id=submission_id, wait_seconds=wait_seconds)
