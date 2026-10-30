"""Actual REPL automatic analysis; directory/open substitutes are explicitly controlled."""
import pytest

from Sprout.execution import file_access
from Sprout.execution.file_snapshot import DirectoryEntry, ResourceIdentity
from Sprout.security.access import AccessDecision
from Sprout.security.layered_policy import LayeredPolicyEngine, PolicyLayer, PolicyRule
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b4_cli_analysis import cli as cli
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.fixture
def automatic(cli, monkeypatch):
    cli.state.inputs = ["分析当前整个项目"]
    cli.state.answers = [".", True]
    policy = LayeredPolicyEngine(workspace=PolicyLayer("auto-host", (
        PolicyRule(action="directory.list", decision=AccessDecision.ALLOW, id="host-directory"),
        PolicyRule(action="file.read", path_glob="*private*", decision=AccessDecision.DENY,
                   id="host-private-deny"),)))
    cli.rig.runtime._policy_engine = policy
    cli.rig.runtime._file_broker._policy = policy
    records = {
        ".": [("src", True), ("src2", True)],
        "src": [("src/main.py", False), ("src/worker.py", False)],
        "src2": [("src2/outside.py", False)],
    }
    async def listing(root, target, limit, authorize):
        relative = target.relative_to(root).as_posix()
        result = []
        for label, directory in records.get(relative, []):
            path = root / label
            if await authorize(path):
                if directory:
                    identity = ResourceIdentity.from_stat(path.stat())
                else:
                    import os
                    with path.open("rb") as stream:
                        identity = ResourceIdentity.from_stat(os.fstat(stream.fileno()))
                result.append(DirectoryEntry(label, directory, identity))
        return tuple(result)
    monkeypatch.setattr(file_access, "directory_entries", listing)
    return cli


async def test_whole_project_natural_language_repl_not_manual_manifest(automatic):
    cli = automatic
    await cli.run()
    cli.create.assert_awaited_once()
    kwargs = cli.create.call_args.kwargs
    assert kwargs["delegation_scope"].allowed_paths == (str(cli.rig.root),)
    assert "directory.list" in kwargs["delegation_scope"].allowed_actions
    assert not kwargs["metadata"].get("required_paths")
    assert cli.state.classification == 1
    assert any("CLI_PROJECT_MAIN_RESULT" in value for value in cli.state.display)
    assert set(cli.captured.opens) == {"src/main.py", "src/worker.py", "src2/outside.py"}
    assert any("覆盖" in value or "coverage" in value.lower() for value in cli.state.display)


async def test_whole_project_can_limit_to_known_subtree(automatic):
    cli = automatic
    cli.state.answers = ["src", True]
    await cli.run()
    assert cli.create.call_args.kwargs["delegation_scope"].allowed_paths == (
        str(cli.rig.root / "src"),)
    assert set(cli.captured.opens) == {"src/main.py", "src/worker.py"}


async def test_whole_project_query_uses_current_discovery(automatic):
    cli = automatic
    cli.state.query = "symbols"
    await cli.run()
    assert any("cli-query" == m.tool_call_id and "main" in m.content
               for m in cli.state.seen if str(m.role) == "tool")


@pytest.mark.parametrize("answers", [["", True], [".", False], ["../outside", True]])
async def test_auto_cancel_or_invalid_does_not_create_task(automatic, answers):
    cli = automatic
    cli.state.answers = answers
    await cli.run()
    cli.create.assert_not_called()
    assert not cli.captured.opens


async def test_auto_requires_real_server_directory_grant(automatic):
    cli = automatic
    from Sprout.security.engine import PolicyEngine
    cli.rig.runtime._policy_engine = PolicyEngine()
    cli.rig.runtime._file_broker._policy = cli.rig.runtime._policy_engine
    await cli.run()
    assert not cli.captured.opens
    assert any("directory" in value.lower() or "目录" in value for value in cli.state.display)


async def test_auto_platform_failure_does_not_publish_success(automatic, monkeypatch):
    cli = automatic
    async def unavailable(*args):
        raise file_access.FileBoundaryError("directory_enumeration_unavailable")
    monkeypatch.setattr(file_access, "directory_entries", unavailable)
    await cli.run()
    assert not cli.captured.opens
    assert not any("CLI_PROJECT_MAIN_RESULT" in value for value in cli.state.display)
