"""Temporal worker entry point for Sprout orchestration."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from Sprout.orchestration.terminal.temporal import TemporalConfig


async def run_task_worker(config: TemporalConfig, *, settings, on_ready=None) -> None:
    """Serve only ordinary task activities, with an explicitly owned Runtime.

    No generic Runtime.start, storage auto-provisioning, growth automation or
    in-process queue consumer. Required infrastructure is deployed separately.
    Cancellation joins the Temporal worker before closing storage.
    """
    from temporalio import activity
    from temporalio.client import Client
    from temporalio.worker import Worker

    from Sprout.orchestration.temporal_codec import node_from_dict
    from Sprout.orchestration.temporal_workflows import SproutExecutionWorkflow
    from Sprout.runtime.factory import create_runtime

    if any(not isinstance(value, str) or not value.strip()
           for value in (config.host, config.namespace, config.task_queue)):
        raise ValueError("Explicit Temporal host, namespace and task queue required")
    metadata_dsn = settings.storage.metadata
    if (not metadata_dsn or metadata_dsn.startswith("memory") or ":memory:" in metadata_dsn):
        raise ValueError("Persistent metadata configuration required")

    runtime = create_runtime(settings)

    @activity.defn(name="run_sprout_node")
    async def execute_node(node):
        return await runtime.execute_node(node_from_dict(node))

    try:
        client = await asyncio.wait_for(
            Client.connect(config.host, namespace=config.namespace), timeout=15)
        async with Worker(client, task_queue=config.task_queue,
                          workflows=[SproutExecutionWorkflow], activities=[execute_node],
                          graceful_shutdown_timeout=timedelta(seconds=10)):
            if on_ready is not None:
                on_ready()
            await asyncio.Future()
    finally:
        await runtime.storage.close()


async def run_worker(config: TemporalConfig | None = None) -> None:
    from temporalio.client import Client
    from temporalio.worker import Worker

    from Sprout.config.loader import load_settings
    from Sprout.orchestration.temporal_workflows import (
        SproutExecutionWorkflow,
        SproutGatewayWorkflow,
        SproutGrowthWorkflow,
        SproutTaskWorkflow,
        run_growth_automation,
        run_sprout_node,
        set_activity_runtime,
    )
    from Sprout.runtime.factory import create_runtime

    config = config or TemporalConfig.from_env()
    settings = load_settings()
    from Sprout.storage.bootstrap import ensure_storage_ready

    await ensure_storage_ready(settings)
    runtime = create_runtime(settings)
    await runtime.start()
    set_activity_runtime(runtime)
    try:
        client = await Client.connect(config.host, namespace=config.namespace)
        worker = Worker(
            client,
            task_queue=config.task_queue,
            workflows=[
                SproutExecutionWorkflow,
                SproutGatewayWorkflow,
                SproutGrowthWorkflow,
                SproutTaskWorkflow,
            ],
            activities=[run_growth_automation, run_sprout_node],
        )
        await worker.run()
    finally:
        try:
            await runtime.stop()
        finally:
            set_activity_runtime(None)
