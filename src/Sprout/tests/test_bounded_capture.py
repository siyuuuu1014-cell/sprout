import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.proc import OutputLimitExceeded, capture_bounded
from Sprout.execution.process_broker import ProcessBroker
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.workspace.models import Workspace


def process(stdout, stderr):
    streams = []
    for value in (stdout, stderr):
        reader = asyncio.StreamReader()
        reader.feed_data(value)
        reader.feed_eof()
        streams.append(reader)
    return SimpleNamespace(stdout=streams[0], stderr=streams[1], wait=AsyncMock())


async def test_exact_combined_budget():
    child = process(b"abc", b"de")
    assert await capture_bounded(child, 5) == (b"abc", b"de")
    child.wait.assert_awaited_once()


@pytest.mark.parametrize("stdout,stderr", [(b"abcdef", b""), (b"", b"abcdef"),
                                          (b"abc", b"def")])
async def test_either_stream_and_combined_overflow(stdout, stderr):
    with pytest.raises(OutputLimitExceeded):
        await capture_bounded(process(stdout, stderr), 5)


async def test_real_broker_limits_child_output(tmp_path):
    policy = SimpleNamespace(decide=lambda request: PolicyDecision(AccessDecision.ALLOW, "fixture"))
    broker = ProcessBroker(policy)
    result = await broker.run(
        Workspace(id="fixture", root=tmp_path),
        (sys.executable, "-c", "import sys; sys.stdout.write('a' * 1000000)"),
        max_output_bytes=1024, timeout_seconds=10)
    assert result.exit_code is None
    assert result.error == "Process output limit exceeded"
    assert not result.stdout and not result.stderr


async def test_invalid_limit_never_spawns(tmp_path, monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    with pytest.raises(ValueError):
        await ProcessBroker(SimpleNamespace()).run(
            Workspace(id="fixture", root=tmp_path), ("unused",), max_output_bytes=-1)
    spawn.assert_not_called()
