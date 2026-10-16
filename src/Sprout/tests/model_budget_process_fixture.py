"""Reserve local metadata allowances in a child process; never call a model."""

import argparse
import asyncio
import json

from Sprout.storage.local.sqlite.metadata import open_metadata_store


async def run(args):
    store = open_metadata_store(args.database)
    try:
        granted = 0
        for index in range(args.attempts):
            granted += bool(await store.reserve_model_request(args.task_id, str(index), args.limit))
        print(json.dumps({"granted": granted}))
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("task_id")
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--attempts", type=int, default=4)
    asyncio.run(run(parser.parse_args()))
