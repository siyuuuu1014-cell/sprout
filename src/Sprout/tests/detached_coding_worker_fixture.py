"""Real coding-worker CLI with explicit offline model and trial authorizations."""

import asyncio
import os

from Sprout.tests.commit_worker_signal_harness import controlled_run
from Sprout.tests.offline_evolution_cli_harness import install_fixture_runtime

if __name__ == "__main__":
    from Sprout.cli.app import main

    install_fixture_runtime(allow_coding_command=os.getenv('SPROUT_A2_CLI_RESUME') != '1')
    asyncio.run = controlled_run
    main()
