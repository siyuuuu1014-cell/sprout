from unittest.mock import AsyncMock

import pytest

from Sprout.execution.coding_router import CodingExecutionRouter
from Sprout.execution.models import ProcessResult
from Sprout.tests.test_coding_container import fixture


async def test_missing_backend_never_launches_host_process(tmp_path, monkeypatch):
    spawn = AsyncMock(side_effect=AssertionError("No host fallback"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    request = fixture(tmp_path)
    result = await CodingExecutionRouter().execute(request, node_id="node")
    assert not result.allowed and result.exit_code is None
    assert result.error == "isolated_coding_backend_not_ready"
    spawn.assert_not_called()


async def test_installed_backend_receives_exact_request_once(tmp_path):
    request = fixture(tmp_path)
    backend = AsyncMock()
    backend.execute.return_value = ProcessResult(request.command, exit_code=0, stdout="actual")
    result = await CodingExecutionRouter(backend).execute(request, node_id="node")
    assert result.stdout == "actual"
    backend.execute.assert_awaited_once_with(request, node_id="node")


async def test_unknown_backend_failure_propagates_without_retry(tmp_path):
    backend = AsyncMock()
    backend.execute.side_effect = TimeoutError("unknown daemon result")
    with pytest.raises(TimeoutError):
        await CodingExecutionRouter(backend).execute(fixture(tmp_path), node_id="node")
    assert backend.execute.await_count == 1


async def test_missing_node_cannot_execute(tmp_path):
    backend = AsyncMock()
    with pytest.raises(ValueError, match="owning node"):
        await CodingExecutionRouter(backend).execute(fixture(tmp_path), node_id="")
    backend.execute.assert_not_called()
