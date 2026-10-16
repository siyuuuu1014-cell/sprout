"""Explicit ownership of preassembled isolated workers; no automatic startup."""

import asyncio
import sys

from Sprout.orchestration.isolated_worker import IsolatedCodingWorker, IsolatedCommitWorker


async def run_owned_workers(*workers):
    """Run once, join every worker before closing its experiment storage.

    Ownership transfers only after all checks succeed. Callers must not reuse
    these Runtime instances. Cancellation never cancels persisted workflows,
    clears execution ownership or retries an activity in this process.
    """
    if not workers or any(type(w) not in (IsolatedCodingWorker, IsolatedCommitWorker)
                          for w in workers):
        raise ValueError("Explicit isolated workers required")
    runtimes = [w._runtime for w in workers]
    if len({id(r) for r in runtimes}) != len(runtimes):
        raise ValueError("Workers must own separate Runtime instances")
    if any(getattr(r, "_started", False) for r in runtimes):
        raise ValueError("Generic-started Runtime cannot transfer ownership")
    if any(getattr(r, "_isolated_lifecycle_owned", False) is True for r in runtimes):
        raise ValueError("Runtime lifecycle already consumed")
    scope = {(w._task_id, w._binding, w._config.host, w._config.namespace) for w in workers}
    if len(scope) != 1 or len({w._config.task_queue for w in workers}) != len(workers):
        raise ValueError("One experiment with distinct worker queues required")
    for runtime in runtimes:
        runtime._isolated_lifecycle_owned = True
    tasks = []
    try:
        tasks = [asyncio.create_task(w.run()) for w in workers]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            if task in done:
                task.result()
        raise RuntimeError("Isolated worker exited unexpectedly")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Attempt every close even if one storage backend fails.
        results = await asyncio.gather(*(r.storage.close() for r in runtimes),
                                       return_exceptions=True)
        # Preserve the original worker failure/cancellation during unwinding.
        if sys.exc_info()[0] is None:
            for result in results:
                if isinstance(result, BaseException):
                    raise result
