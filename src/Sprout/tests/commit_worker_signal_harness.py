"""Test-only cross-platform SIGINT stimulus for a real CLI worker process.

The CLI/configuration/Runtime are unchanged. A stdin command schedules an actual
SIGINT on the event-loop thread, avoiding Windows console attachment side effects.
This proves the Python signal path, not a physical terminal's key delivery.
"""

import asyncio
import signal
import sys

from Sprout.cli.app import main

_run = asyncio.run


def controlled_run(coro):
    async def wrapped():
        async def interrupt():
            line = await asyncio.to_thread(sys.stdin.readline)
            if line.strip() == "interrupt":
                signal.raise_signal(signal.SIGINT)

        control = asyncio.create_task(interrupt())
        try:
            return await coro
        finally:
            control.cancel()
            await asyncio.gather(control, return_exceptions=True)

    return _run(wrapped())


if __name__ == "__main__":
    asyncio.run = controlled_run
    main()
