"""Exercise the real bound child; no external services or user's database."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution import acceptance
from Sprout.execution import process_broker as broker_module
from Sprout.execution.process_broker import ProcessBroker
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.security.approval import ApprovalManager
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.workspace.models import Workspace


def contract():
    return AcceptanceSpec.from_payload({"cases": [{
        "id": "normal", "target": "bound_example.app:echo", "args": [2], "expected_output": 2,
    }]})


def fixture(tmp_path, source=None, parent=True):
    (tmp_path / "tests").mkdir()
    if parent:
        (tmp_path / "bound_example").mkdir()
        (tmp_path / "bound_example/__init__.py").write_text("", encoding="utf-8")
        if source is not None:
            (tmp_path / "bound_example/app.py").write_text(source, encoding="utf-8")
    (tmp_path / "tests/test_acceptance.py").write_text(
        acceptance.render_acceptance_tests(contract()), encoding="utf-8")
    return Workspace(id="workspace", root=tmp_path)


@pytest.mark.parametrize("source,parent,expected", [
    (None, True, "target_module_not_implemented"),
    ("value = 1\n", True, "requirement_not_implemented"),
    ("def echo(value):\n    return 0\n", True, "assertion_failed"),
    ("def echo(value):\n    return value\n", True, "passed"),
    ("def echo(value):\n    raise AttributeError('internal')\n", True, "unknown"),
    ("raise AttributeError('import')\n", True, "unknown"),
    ("import missing_bound_dependency\n", True, "unknown"),
    ("raise ModuleNotFoundError(name='bound_example.app')\n", True, "unknown"),
    (None, False, "unknown"),
])
async def test_actual_child_red_baseline_boundaries(tmp_path, source, parent, expected):
    workspace = fixture(tmp_path, source, parent)
    broker = ProcessBroker(SimpleNamespace(
        decide=lambda request: PolicyDecision(AccessDecision.ALLOW, "test")))
    binding = acceptance.local_executor_binding()
    result = await broker.run_local_acceptance(
        workspace, contract(), "tests/test_acceptance.py", "normal", binding)
    assert result.started, result.error
    assert acceptance.local_acceptance_evidence(result, binding) == expected, result.stdout


async def test_bound_approval_does_not_reuse_legacy_or_upstream(tmp_path, monkeypatch):
    workspace = fixture(tmp_path, "def echo(value):\n    return value\n")
    manager = ApprovalManager(MemoryOperationalStore())
    broker = ProcessBroker(SimpleNamespace(
        decide=lambda request: PolicyDecision(AccessDecision.REQUIRE_APPROVAL, "review")),
        approvals=manager)
    spawn = AsyncMock(side_effect=AssertionError("No child before approval"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    kwargs = dict(task_id="task", source="cli", requested_by="owner", preflight_only=True)
    legacy = await broker.run(workspace, ("python", "-m", "pytest"), **kwargs)
    await manager.decide(legacy.approval_id, True, decided_by="owner", channel="cli")
    binding = acceptance.local_executor_binding()
    pending = await broker.run_local_acceptance(
        workspace, contract(), "tests/test_acceptance.py", "normal", binding,
        upstream_approval_granted=True, **kwargs)
    assert pending.needs_approval and not pending.allowed
    await manager.decide(pending.approval_id, True, decided_by="owner", channel="cli")
    approved = await broker.run_local_acceptance(
        workspace, contract(), "tests/test_acceptance.py", "normal", binding, **kwargs)
    assert approved.allowed
    changed = {**binding, "fingerprint": "0" * 64}
    blocked = await broker.run_local_acceptance(
        workspace, contract(), "tests/test_acceptance.py", "normal", changed, **kwargs)
    assert not blocked.allowed
    broker._policy.decide = lambda request: PolicyDecision(AccessDecision.DENY, "deny")
    denied = await broker.run_local_acceptance(
        workspace, contract(), "tests/test_acceptance.py", "normal", binding, **kwargs)
    assert not denied.allowed
    spawn.assert_not_called()


async def test_full_executable_argv_ignores_unusable_path(tmp_path, monkeypatch):
    import sys

    workspace = fixture(tmp_path, "def echo(value):\n    return value\n")
    monkeypatch.setenv("PATH", str(tmp_path / "no-python-here"))
    binding = acceptance.local_executor_binding()
    broker = ProcessBroker(SimpleNamespace(
        decide=lambda request: PolicyDecision(AccessDecision.ALLOW, "test")))
    result = await broker.run_local_acceptance(
        workspace, contract(), "tests/test_acceptance.py", "normal", binding)
    assert result.command[0] == sys.executable
    assert acceptance.local_acceptance_evidence(result, binding) == "passed"


@pytest.mark.parametrize("mutation", ["oracle", "target", "collection"])
async def test_frozen_identity_and_collection_fail_closed(tmp_path, mutation):
    workspace = fixture(tmp_path, "def echo(value):\n    return value\n")
    path = tmp_path / "tests/test_acceptance.py"
    if mutation == "oracle":
        path.write_text(path.read_text(encoding="utf-8").replace("assert actual == 2",
                                                               "assert actual == 0"),
                        encoding="utf-8")
    elif mutation == "target":
        path.write_text(path.read_text(encoding="utf-8").replace("bound_example.app", "other.app"),
                        encoding="utf-8")
    else:
        (tmp_path / "conftest.py").write_text("this is invalid python !!!", encoding="utf-8")
    binding = acceptance.local_executor_binding()
    broker = ProcessBroker(SimpleNamespace(
        decide=lambda request: PolicyDecision(AccessDecision.ALLOW, "test")))
    result = await broker.run_local_acceptance(
        workspace, contract(), "tests/test_acceptance.py", "normal", binding)
    assert acceptance.local_acceptance_evidence(result, binding) == "unknown"


@pytest.mark.parametrize("ambiguity", ["parent_path", "empty_target_directory"])
async def test_dynamic_or_ambiguous_target_resolution_is_not_a_red_baseline(tmp_path, ambiguity):
    workspace = fixture(tmp_path)
    if ambiguity == "parent_path":
        (tmp_path / "bound_example/__init__.py").write_text("__path__ = []\n", encoding="utf-8")
    else:
        (tmp_path / "bound_example/app").mkdir()
    binding = acceptance.local_executor_binding()
    broker = ProcessBroker(SimpleNamespace(
        decide=lambda request: PolicyDecision(AccessDecision.ALLOW, "test")))
    result = await broker.run_local_acceptance(
        workspace, contract(), "tests/test_acceptance.py", "normal", binding)
    assert acceptance.local_acceptance_evidence(result, binding) == "unknown"


def test_unavailable_pytest_and_bad_bindings_fail_closed(monkeypatch):
    binding = acceptance.local_executor_binding()
    assert acceptance.local_executor_applicability(binding) == "match"
    assert acceptance.local_executor_applicability(None) == "unknown"
    monkeypatch.setattr(acceptance, "local_executor_binding",
                        lambda: (_ for _ in ()).throw(ImportError("pytest_missing")))
    assert acceptance.local_executor_applicability(binding) == "unknown"


async def test_changed_server_identity_cannot_reuse_approved_identity(tmp_path, monkeypatch):
    workspace = fixture(tmp_path)
    manager = ApprovalManager(MemoryOperationalStore())
    broker = ProcessBroker(SimpleNamespace(
        decide=lambda request: PolicyDecision(AccessDecision.REQUIRE_APPROVAL, "review")),
        approvals=manager)
    spawn = AsyncMock(side_effect=AssertionError("No child before approval"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    kwargs = dict(task_id="task", source="cli", requested_by="owner", preflight_only=True)
    first = acceptance.local_executor_binding()
    pending = await broker.run_local_acceptance(
        workspace, contract(), "tests/test_acceptance.py", "normal", first, **kwargs)
    await manager.decide(pending.approval_id, True, decided_by="owner", channel="cli")
    second = {**first, "fingerprint": "1" * 64}
    monkeypatch.setattr(acceptance, "local_executor_binding", lambda: second)
    monkeypatch.setattr(broker_module, "local_executor_binding", lambda: second)
    denied = await broker.run_local_acceptance(
        workspace, contract(), "tests/test_acceptance.py", "normal", first, **kwargs)
    assert not denied.allowed
    new = await broker.run_local_acceptance(
        workspace, contract(), "tests/test_acceptance.py", "normal", second, **kwargs)
    assert new.needs_approval and new.approval_id != pending.approval_id
    spawn.assert_not_called()
