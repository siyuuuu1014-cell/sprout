from unittest.mock import AsyncMock

from Sprout.execution.models import SandboxRef
from Sprout.execution.sandbox_tool import SandboxGitTool


async def test_isolated_git_tool_never_spawns_host_git(tmp_path, monkeypatch):
    run = AsyncMock(side_effect=AssertionError("Host Git must not run"))
    monkeypatch.setattr("Sprout.execution.sandbox_tool.run_capture", run)
    tool = SandboxGitTool(SandboxRef("sandbox", "fixture", tmp_path), allow_host_git=False)
    result = await tool.invoke({"operation": "status"})
    assert not result.ok
    run.assert_not_called()
