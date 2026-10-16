"""Explicit test subprocesses: real Temporal, no model or application data."""

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from Sprout.orchestration.submission_observation import observe_submission
from Sprout.orchestration.temporal_workflows import SproutExecutionWorkflow


async def main(args):
    record = json.loads(args.record.read_text(encoding="utf-8"))
    assert record["host"] == "127.0.0.1:17233"
    if args.role == "observe":
        print(json.dumps(await observe_submission(record)))
        return
    gate = args.record.parent / "release"
    entered = args.record.parent / "entered"

    @activity.defn(name="run_sprout_node")
    async def execute(node):
        entered.touch(exist_ok=False)
        while not gate.exists():
            await asyncio.sleep(0.05)
        return {"node_id": node["id"], "status": "completed", "output": {"fixture": True}}

    client = await Client.connect(record["host"], namespace=record["namespace"])
    async with Worker(client, task_queue=record["task_queue"],
                      workflows=[SproutExecutionWorkflow], activities=[execute],
                      graceful_shutdown_timeout=timedelta(seconds=5)):
        print("ready", flush=True)
        await asyncio.to_thread(sys.stdin.readline)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("worker", "observe"))
    parser.add_argument("record", type=Path)
    asyncio.run(main(parser.parse_args()))
