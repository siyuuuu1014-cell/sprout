"""Explicit server grants, never file-read or legacy wildcard grants."""
from dataclasses import replace

import pytest

from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.security.engine import PolicyEngine
from Sprout.security.layered_policy import LayeredPolicyEngine, PolicyLayer, PolicyRule
from Sprout.task.models import DelegationScope
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_pa2_b1_binding import node_scope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.workspace.models import ResourceKind, ResourceRef


@pytest.mark.parametrize("rules,expected", [
    ((), AccessDecision.REQUIRE_APPROVAL),
    ((PolicyRule(action="*", decision=AccessDecision.ALLOW),),
     AccessDecision.REQUIRE_APPROVAL),
    ((PolicyRule(action="directory.list", decision=AccessDecision.ALLOW, id="listing"),),
     AccessDecision.ALLOW),
    ((PolicyRule(action="*", decision=AccessDecision.ALLOW),
      PolicyRule(action="directory.list", decision=AccessDecision.ALLOW, id="listing")),
     AccessDecision.ALLOW),
    ((PolicyRule(action="*", decision=AccessDecision.DENY),
      PolicyRule(action="directory.list", decision=AccessDecision.ALLOW, id="listing")),
     AccessDecision.DENY),
])
def test_directory_requires_exact_server_grant(rules, expected):
    request = ActionRequest(action=ActionType.DIRECTORY_LIST,
                            resource=ResourceRef("ws", "/project/src", ResourceKind.OTHER))
    engine = LayeredPolicyEngine(workspace=PolicyLayer("workspace", rules))
    result = engine.decide(request)
    assert result.decision is expected
    if expected is AccessDecision.ALLOW:
        assert "directory:grant:listing" in result.matched_rules
    denied = replace(request, scope=DelegationScope(allowed_actions=("file.read",)))
    assert engine.decide(denied).decision is AccessDecision.DENY


def test_file_read_is_not_directory_list():
    request = ActionRequest(action=ActionType.FILE_READ,
                            resource=ResourceRef("ws", "/project/a.py", ResourceKind.SOURCE))
    assert PolicyEngine().decide(request).decision is AccessDecision.ALLOW
    assert PolicyEngine().decide(replace(request, action=ActionType.DIRECTORY_LIST)).decision \
        is AccessDecision.REQUIRE_APPROVAL


@pytest.mark.parametrize("grant", ["exact", "wildcard", "none", "deny", "unavailable",
                                    "hidden", "budget", "malformed"])
async def test_discovery_actual_tool_result(rig, captured, monkeypatch, grant):
    from unittest.mock import AsyncMock

    from Sprout.execution import file_access
    from Sprout.execution.file_access import FileBoundaryError
    from Sprout.execution.file_snapshot import DirectoryEntry, ResourceIdentity

    rig.task = replace(rig.task, delegation_scope=replace(
        rig.task.delegation_scope,
        allowed_actions=frozenset({"tool.invoke", "file.read", "directory.list"})))
    await rig.storage.metadata.save_task(rig.task)
    rules = (() if grant == "none" else (PolicyRule(
        action="*" if grant == "wildcard" else "directory.list",
        decision=AccessDecision.DENY if grant == "deny" else AccessDecision.ALLOW,
        id="host-list"),))
    policy = LayeredPolicyEngine(workspace=PolicyLayer("workspace", rules))
    rig.runtime._file_broker._policy = policy
    calls = []
    snapshots = []
    backend_errors = []
    from Sprout.tests import test_r04_b2_capture as capture_fixture

    original_read = capture_fixture.read_fd
    def observed_read(fd, options):
        import os

        before = ResourceIdentity.from_stat(os.fstat(fd))
        try:
            return original_read(fd, options)
        except FileBoundaryError as exc:
            backend_errors.append((str(exc), options.expected, before,
                                   ResourceIdentity.from_stat(os.fstat(fd))))
            raise
    monkeypatch.setattr(capture_fixture, "read_fd", observed_read)
    original_snapshot = rig.runtime._file_broker.read_snapshot
    async def snapshot(*args, **kwargs):
        result = await original_snapshot(*args, **kwargs)
        snapshots.append((kwargs.get("expected_identity"), result.identity, result.reason))
        return result
    monkeypatch.setattr(rig.runtime._file_broker, "read_snapshot", snapshot)
    async def listing(root, target, limit, authorize):
        import os

        calls.append(target)
        if grant == "unavailable":
            raise FileBoundaryError("directory_enumeration_unavailable")
        if grant == "malformed":
            return ["untrusted backend"]
        if grant == "budget":
            for _ in range(limit + 1):
                await authorize(root / "src/main.py")
        if grant == "hidden":
            assert not await authorize(root / "src/private/denied.py")
        path = "src/main.py"
        assert await authorize(root / path)
        # This Windows test double must use the same descriptor identity domain
        # as the mocked snapshot reader. Path.stat().ctime and fstat().ctime
        # differ here; this does NOT emulate/prove a native Windows enumerator.
        with (root / path).open("rb") as stream:
            identity = ResourceIdentity.from_stat(os.fstat(stream.fileno()))
        return (DirectoryEntry(path, False, identity),)
    monkeypatch.setattr(file_access, "directory_entries", listing)
    if grant == "budget":
        from Sprout.execution.file_snapshot import DirectoryReadLimits

        enumerate_files = rig.runtime._file_broker.enumerate_readable
        async def bounded(*args, **kwargs):
            return await enumerate_files(*args, **kwargs, limits=DirectoryReadLimits(max_entries=2))
        monkeypatch.setattr(rig.runtime._file_broker, "enumerate_readable", bounded)
    manager = AsyncMock()
    rig.executor.approvals = manager
    rig.runtime._file_broker._approvals = manager
    before = await rig.storage.operational.list_approvals()
    with node_scope(rig):
        tool = await rig.runtime._issue_project_analysis_tool(discover=True)
        executor = rig.executor.with_scoped_tools({tool.spec.name: tool}, strict=True)
        result = await executor.execute(tool.spec.name, {"path": str(rig.root / "src")})
    assert result.ok is (grant in {"exact", "hidden"}), repr((snapshots, backend_errors))
    if result.ok:
        assert "main" in result.to_text()
        assert result.data["coverage"]["mode"] == "discovery"
        assert not result.data["coverage"]["complete_project"]
        assert captured.opens == ["src/main.py"]
        assert "denied.py" not in result.to_text()
    else:
        assert not captured.opens
    if grant in {"none", "wildcard", "deny"}:
        assert not calls
    manager.request.assert_not_called()
    manager.is_approved.assert_not_called()
    assert await rig.storage.operational.list_approvals() == before


async def test_windows_native_enumeration_is_unavailable_not_mock_support(tmp_path):
    import os

    from Sprout.execution.file_access import FileBoundaryError, directory_entries

    if os.name != "nt":
        return  # This assertion concerns the current Windows runner only.
    async def authorize(path):
        raise AssertionError("Unsupported native backend reached authorization callback")
    with pytest.raises(FileBoundaryError, match="directory_enumeration_unavailable"):
        await directory_entries(tmp_path, tmp_path, 10, authorize)


@pytest.mark.parametrize("entry", ["tool", "broker"])
async def test_direct_bound_discovery_respects_tool_deny_before_enumeration(
    rig, monkeypatch, entry,
):
    from unittest.mock import AsyncMock

    from Sprout.execution.models import SandboxRef
    from Sprout.security.access import PolicyDecision
    from Sprout.tools.context_keys import _project_analysis_call

    rig.task = replace(rig.task, delegation_scope=replace(
        rig.task.delegation_scope,
        allowed_actions=frozenset({"tool.invoke", "file.read", "directory.list"})))
    await rig.storage.metadata.save_task(rig.task)
    class Deny:
        def decide(self, request):
            if request.action is ActionType.TOOL_INVOKE:
                return PolicyDecision(AccessDecision.DENY, "host tool deny")
            return PolicyEngine().decide(request)
    rig.runtime._policy_engine = Deny()
    rig.runtime._file_broker._policy = LayeredPolicyEngine(workspace=PolicyLayer("workspace", (
        PolicyRule(action="directory.list", decision=AccessDecision.ALLOW, id="list"),)))
    native = AsyncMock(return_value=())
    monkeypatch.setattr("Sprout.execution.file_access.directory_entries", native)
    with node_scope(rig):
        tool = await rig.runtime._issue_project_analysis_tool(discover=True)
        with _project_analysis_call(tool) as frame:
            arguments = {"path": str(rig.root / "src")}
            if entry == "tool":
                result = await tool.invoke(arguments)
            else:
                subject = await rig.runtime._project_analysis_request(tool, arguments, frame, "")
                result = await rig.runtime._file_broker.enumerate_readable(
                    SandboxRef(rig.workspace.id, "restricted_analysis", rig.root),
                    "src", subject=subject)
            assert not result.ok
    native.assert_not_called()
