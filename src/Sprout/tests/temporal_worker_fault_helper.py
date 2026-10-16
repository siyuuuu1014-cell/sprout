"""Test-only worker interrupted after execution, before guard completion."""

import asyncio
import os
import sys
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from Sprout.orchestration.temporal_workflows import (
    SproutExecutionWorkflow,
    run_sprout_node,
    set_activity_runtime,
)
from Sprout.runtime.factory import create_runtime
from Sprout.runtime.nodes import NodeExecutor
from Sprout.tests.test_temporal_integration import _isolated_settings


async def main():
    directory = Path(sys.argv[1]).resolve()
    assert os.environ["TEMPORAL_HOST"] == "127.0.0.1:17233"
    original = NodeExecutor.execute_node

    async def interrupt(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        # Parent kills the process after this test-owned side effect, without
        # graceful cancellation or guard completion. Never writes user files.
        with (directory / "effect.log").open("a", encoding="utf-8") as stream:
            stream.write("executed\n")
        await asyncio.Event().wait()
        return result

    NodeExecutor.execute_node = interrupt
    runtime = create_runtime(_isolated_settings(directory))
    await runtime.start()
    set_activity_runtime(runtime)
    try:
        client = await Client.connect(
            os.environ["TEMPORAL_HOST"], namespace=os.environ["TEMPORAL_NAMESPACE"]
        )
        async with Worker(
            client,
            task_queue=os.environ["TEMPORAL_TASK_QUEUE"],
            workflows=[SproutExecutionWorkflow],
            activities=[run_sprout_node],
        ):
            await asyncio.Event().wait()
    finally:
        set_activity_runtime(None)
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
