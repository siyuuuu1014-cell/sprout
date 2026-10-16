"""One explicit test process: fail connect before any scheduler request."""

from temporalio.client import Client

from Sprout.cli.app import main


async def unavailable(*args, **kwargs):
    raise ConnectionError("Explicit test fixture: connection unavailable before submission")


if __name__ == "__main__":
    Client.connect = unavailable
    main()
