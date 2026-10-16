"""Explicit integration subprocesses using real Gateway/Runtime/Temporal activity."""

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from Sprout.config.loader import default_settings
from Sprout.gateway.identity import Principal
from Sprout.gateway.sdk_gateway import SDKGateway
from Sprout.orchestration.temporal_workflows import (
    SproutExecutionWorkflow,
    run_sprout_node,
    set_activity_runtime,
)
from Sprout.orchestration.terminal.temporal import TemporalConfig, TemporalOrchestratorBackend
from Sprout.runtime.factory import create_runtime

OWNER = Principal(user_id="integration-owner", authenticated=True, source="test-host")


def make_runtime(directory):
    settings = default_settings()
    settings.model.provider = "echo"
    settings.model.model = "echo-1"
    settings.skills_dir = str(directory / "skills")
    for slot in ("operational", "metadata", "session", "context", "usage"):
        setattr(settings.storage, slot, f"sqlite:///{directory / (slot + '.db')}")
    settings.storage.knowledge = "memory://"
    settings.storage.observations.enabled = False
    settings.storage.trajectory_dir = str(directory / "trajectory")
    settings.storage.blobs_dir = str(directory / "blobs")
    settings.storage.project_root = str(directory / "projects")
    settings.security.audit.path = str(directory / "audit.jsonl")
    settings.memory.home = str(directory / "memory")
    settings.memory.enabled = False
    return create_runtime(settings)


async def main(args):
    route = json.loads(args.route.read_text(encoding="utf-8"))
    assert route["host"] == "127.0.0.1:17233"
    runtime = make_runtime(args.route.parent)
    runtime._orchestrator_backend = TemporalOrchestratorBackend(TemporalConfig(
        host=route["host"], namespace=route["namespace"], task_queue=route["task_queue"]))
    gateway = SDKGateway(runtime, principal=OWNER)
    try:
        if args.role == "submit":
            result = await gateway.submit_persisted_task(route["task_id"])
        elif args.role == "observe":
            result = await gateway.observe_task(route["task_id"], wait_seconds=30)
        elif args.role == "sync":
            result = await gateway.sync_task(route["task_id"])
        elif args.role == "report":
            result = await gateway.task_report(route["task_id"])
        else:
            set_activity_runtime(runtime)
            client = await Client.connect(route["host"], namespace=route["namespace"])
            async with Worker(client, task_queue=route["task_queue"],
                              workflows=[SproutExecutionWorkflow], activities=[run_sprout_node],
                              graceful_shutdown_timeout=timedelta(seconds=5)):
                print("ready", flush=True)
                await asyncio.to_thread(sys.stdin.readline)
            return
        print(json.dumps(result), flush=True)
    finally:
        set_activity_runtime(None)
        await runtime.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("submit", "observe", "sync", "report", "worker"))
    parser.add_argument("route", type=Path)
    asyncio.run(main(parser.parse_args()))
