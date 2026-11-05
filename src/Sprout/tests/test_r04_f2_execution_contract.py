"""Real Runtime/Guard registries, controlled OS/process substitutes; not OS proof."""
import asyncio
import copy
import os
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution import host_acceptance as host
from Sprout.execution.acceptance import (
    local_executor_binding,
    render_acceptance_demo,
    render_acceptance_tests,
)
from Sprout.execution.file_snapshot import ResourceIdentity, read_fd
from Sprout.execution.models import SandboxRef
from Sprout.execution.node_result_source import payload_digest
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import DelegationScope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


class UnitPin:
    """Fixture-only pin substitute; native pinning is separately tested."""
    def __init__(self, root):
        self.root, self.leaves = Path(root), {}
        info = self.root.stat()
        self.identity = ("unit", info.st_dev, info.st_ino)
        self.spawn = {"cwd": str(root)}

    def pin_file(self, path):
        identity = ResourceIdentity.from_stat((self.root / path).stat())
        self.leaves.setdefault(path, identity)
        return identity

    def check(self):
        for path, identity in self.leaves.items():
            if ResourceIdentity.from_stat((self.root / path).stat()) != identity:
                raise PermissionError("fixture_identity_changed")


@pytest.fixture
async def execution(rig, monkeypatch):
    root = rig.root.parent / "controlled-worktree"
    root.mkdir()
    spec = AcceptanceSpec.from_payload({"cases": [dict(
        id="normal", target="src.app:summarize", args=[[]], expected_output={})]})
    files = {"src/__init__.py": "", "src/app.py": "def summarize(x): return {}\n",
             "tests/frozen.py": render_acceptance_tests(spec),
             "examples/demo.py": render_acceptance_demo(spec)}
    for base in (root, rig.root):
        for path, content in files.items():
            target = base / path
            target.parent.mkdir(exist_ok=True)
            target.write_text(content, encoding="utf-8")
    rig.task = replace(rig.task, delegation_scope=DelegationScope(
        allowed_actions=frozenset({"tool.invoke", "file.read", "file.write", "process.run"}),
        allowed_paths=tuple(files)), metadata={**rig.task.metadata, "execution_mode": "code",
                                             "required_paths": list(files)})
    frozen = {"version": 1, "subject": host.binding_subject(rig.task, rig.workspace),
              "spec": spec.to_payload(), "spec_sha256": payload_digest(spec.to_payload()),
              "test_path": "tests/frozen.py", "demo_path": "examples/demo.py",
              "resources": {path: {"digest": payload_digest(content),
                  "identity": asdict(ResourceIdentity.from_stat((rig.root / path).stat()))}
                  for path, content in files.items()
                  if path in {"tests/frozen.py", "examples/demo.py"}}}
    # Actual content digest, not the JSON-string digest above.
    from hashlib import sha256
    for path, resource in frozen["resources"].items():
        resource["digest"] = sha256((rig.root / path).read_bytes()).hexdigest()
        with (rig.root / path).open("rb") as stream:
            resource["identity"] = asdict(ResourceIdentity.from_stat(os.fstat(stream.fileno())))
    frozen["sha256"] = payload_digest(frozen)
    rig.task = replace(rig.task, metadata={**rig.task.metadata, "host_acceptance_v1": frozen})
    await rig.storage.metadata.save_task(rig.task)

    @contextmanager
    def pin(path):
        value = UnitPin(path)
        yield value
    monkeypatch.setattr(host, "pin_execution_directory", pin)

    async def read(sandbox, target, request, operation, **kwargs):
        assert operation == "read"
        return True, target.read_text(encoding="utf-8")
    monkeypatch.setattr(rig.runtime._file_broker, "_access", read)

    def capture(root, target, operation, authorize, *, content, **kwargs):
        assert root == rig.root and operation == "snapshot"
        authorize()
        with target.open("rb") as stream:
            return read_fd(stream.fileno(), content)
    monkeypatch.setattr("Sprout.execution.file_broker.file_operation", capture)
    sandbox = SandboxRef(id="sandbox", kind="git_worktree", root=root, worktree_ref="fixture")
    pending = ExecutionNode(id="contract-sandbox", task_id=rig.task.id, type=NodeType.SANDBOX)
    await rig.storage.metadata.save_execution_node(pending)
    assert await rig.commission(pending, "sandbox-owner", "")
    running = await rig.storage.metadata.get_execution_node(pending.id)
    output = dict(id=sandbox.id, kind=sandbox.kind, root=str(root),
                  worktree_ref=sandbox.worktree_ref)
    with rig.runtime._project_analysis_scope(rig.task, rig.workspace, running) as scope:
        await rig.runtime._register_frozen_sandbox(sandbox)
        rig.runtime._remember_node_product(scope, output)
    assert await rig.storage.metadata.finish_execution_node(replace(running,
        status=NodeStatus.COMPLETED, metadata={**running.metadata, "output": output}),
        "sandbox-owner", "completed")
    await rig.runtime._persist_node_products()
    pending = ExecutionNode(id="contract-design", task_id=rig.task.id, type=NodeType.TEST_DESIGN,
                            dependencies=(running.id,))
    await rig.storage.metadata.save_execution_node(pending)
    assert await rig.commission(pending, "design-owner", "")
    rig.node = await rig.storage.metadata.get_execution_node(pending.id)
    process = SimpleNamespace(returncode=0,
        stdout=SimpleNamespace(read=AsyncMock(return_value=b"")),
        stderr=SimpleNamespace(read=AsyncMock(return_value=b"")),
        communicate=AsyncMock(return_value=(b"", b"")), wait=AsyncMock(return_value=0))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    broker = rig.runtime._process_broker
    original = broker._policy.decide
    requests = []
    mode = [AccessDecision.ALLOW]

    def decide(request):
        requests.append(request)
        if request.action is ActionType.PROCESS_RUN:
            if not request.scope.permits("process.run", request.resource.path):
                return PolicyDecision(AccessDecision.DENY, "scope denied")
            return PolicyDecision(mode[0], "controlled test policy")
        return original(request)
    monkeypatch.setattr(broker._policy, "decide", decide)
    yield SimpleNamespace(**locals())


async def issue(e):
    return await e.rig.runtime._issue_frozen_execution(
        e.root, e.spec, "tests/frozen.py", "normal", local_executor_binding())


async def run(e, permit, **extra):
    return await e.broker.run_local_acceptance(e.rig.workspace, e.spec, "tests/frozen.py",
        "normal", local_executor_binding(), execution_contract=permit,
        cwd=extra.pop("cwd", e.root), task_id=e.rig.task.id, actor=e.rig.principal,
        scope=e.rig.task.delegation_scope, source="cli", requested_by=e.rig.principal.user_id,
        resource_scope=e.rig.workspace.id, session_id=e.rig.session.id, **extra)


async def test_valid_target_and_independent_cwd_contract(execution, monkeypatch):
    e = execution
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-only-not-a-key")
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        permit = await issue(e)
        result = await run(e, permit)
        assert result.started and result.exit_code == 0
        assert not (await run(e, permit)).allowed  # consumed, never reusable
    assert e.spawn.await_count == 1
    kwargs = e.spawn.call_args.kwargs
    assert kwargs["cwd"] == str(e.root) and "DEEPSEEK_API_KEY" not in kwargs["env"]
    assert e.rig.task.delegation_scope.permits("process.run", str(e.root)) is False
    request = next(r for r in e.requests if r.action is ActionType.PROCESS_RUN)
    assert request.actor == e.rig.principal and request.task_id == e.rig.task.id
    assert request.arguments["cwd"] == str(e.root)


@pytest.mark.parametrize("change", ["task", "principal", "owner", "scope", "test", "demo",
    "copy", "cwd", "expired", "executor", "policy", "source", "parent", "other-worktree",
    "alias", "spec", "extra-argv", "secret-env", "original-test", "directory-identity",
    "timeout-budget", "output-budget"])
async def test_invalid_contract_never_spawns(execution, change, monkeypatch):
    e = execution
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        permit = await issue(e)
        task = e.rig.task
        if change == "task":
            task = replace(task, workspace_id="other")
        if change == "principal":
            task = replace(task, actor=replace(task.actor, user_id="other"))
        if change == "source":
            task = replace(task, source="forged")
        if change == "scope":
            task = replace(task, delegation_scope=replace(task.delegation_scope,
                denied_actions=frozenset({"process.run"})))
        await e.rig.storage.metadata.save_task(task)
        if change == "owner":
            original = e.rig.storage.metadata.get_execution_node

            async def changed_owner(node_id):
                value = await original(node_id)
                return replace(value, metadata={"execution_guard": {"owner": "forged"}})
            monkeypatch.setattr(e.rig.storage.metadata, "get_execution_node", changed_owner)
        if change in {"test", "demo"}:
            path = "tests/frozen.py" if change == "test" else "examples/demo.py"
            (e.root / path).write_text("tampered", encoding="utf-8")
        if change == "copy":
            permit = copy.copy(permit)
        if change == "expired":
            e.rig.runtime._frozen_executions[permit]["expires"] = 0
        if change == "executor":
            monkeypatch.setattr("Sprout.execution.process_broker.local_executor_applicability",
                                lambda value: "mismatch")
        if change == "policy":
            e.mode[0] = AccessDecision.DENY
        if change == "original-test":
            (e.rig.root / "tests/frozen.py").write_text("tampered", encoding="utf-8")
        if change == "directory-identity":
            e.rig.runtime._frozen_executions[permit]["root_identity"] = ("changed",)
        extra = {}
        if change in {"cwd", "parent", "other-worktree", "alias"}:
            extra["cwd"] = {"cwd": e.rig.root, "parent": e.root.parent,
                "other-worktree": e.root.parent / "another", "alias": e.root / "link"}[change]
        if change == "extra-argv":
            extra["command"] = ("cmd", "/c", "not-authorized")
        if change == "secret-env":
            extra["secret_names"] = ("DEEPSEEK_API_KEY",)
        if change == "timeout-budget":
            extra["timeout_seconds"] = 61
        if change == "output-budget":
            extra["max_output_bytes"] = 262145
        if change == "spec":
            e.spec = AcceptanceSpec.from_payload({"cases": [dict(id="normal",
                target="os:system", args=["not-authorized"], expected_output=0)]})
        result = await run(e, permit, **extra)
        assert not result.allowed
    e.spawn.assert_not_called()


async def test_approval_stops_and_plain_run_does_not_inherit(execution):
    e = execution
    e.mode[0] = AccessDecision.REQUIRE_APPROVAL
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        pending = await run(e, await issue(e))
        assert pending.needs_approval and not pending.started
        plain = await e.broker.run(e.rig.workspace, ("python", "-c", "print(1)"),
            cwd=e.root, scope=e.rig.task.delegation_scope, actor=e.rig.principal)
        assert not plain.allowed and not plain.approval_id
    e.spawn.assert_not_called()


async def test_copied_async_frame_and_forged_contract_refuse(execution):
    e = execution
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        permit = await issue(e)
        assert not (await asyncio.create_task(run(e, permit))).allowed
        assert not (await run(e, host._FrozenExecutionContract(e.rig.runtime))).allowed
    assert not (await run(e, permit)).allowed
    e.spawn.assert_not_called()


def test_launcher_disables_bytecode_before_pytest_import(monkeypatch):
    import json
    import sys

    from Sprout.execution.acceptance import _local_acceptance_main

    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.setattr(sys, "argv", ["trusted-launcher", "not-json"])
    with pytest.raises(json.JSONDecodeError):
        _local_acceptance_main()
    assert sys.dont_write_bytecode is True


@pytest.mark.parametrize("change", ["scope", "executor", "policy"])
async def test_approval_await_revalidates_before_spawn(execution, monkeypatch, change):
    e = execution
    e.mode[0] = AccessDecision.REQUIRE_APPROVAL

    async def approve_then_change(*args, **kwargs):
        if change == "scope":
            await e.rig.storage.metadata.save_task(replace(e.rig.task, delegation_scope=replace(
                e.rig.task.delegation_scope, denied_actions=frozenset({"process.run"}))))
        if change == "executor":
            monkeypatch.setattr("Sprout.execution.process_broker.local_executor_applicability",
                                lambda value: "mismatch")
        if change == "policy":
            e.mode[0] = AccessDecision.DENY
        return True  # Controlled simulation of an approval followed by revocation.
    monkeypatch.setattr(e.broker._approvals, "is_approved", approve_then_change)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        assert not (await run(e, await issue(e))).allowed
    e.spawn.assert_not_called()


async def test_cancellation_consumes_contract_without_retry(execution):
    e = execution
    e.spawn.side_effect = asyncio.CancelledError
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        permit = await issue(e)
        with pytest.raises(asyncio.CancelledError):
            await run(e, permit)
        assert not (await run(e, permit)).allowed
    assert e.spawn.await_count == 1


@pytest.mark.parametrize("source", ["import unselected_dependency", "import importlib\n"
    "def later(): return importlib.import_module('unselected_dependency')"])
def test_unselected_dependencies_not_inferred_from_test_permission(source):
    spec = AcceptanceSpec.from_payload({"cases": [dict(id="case", target="src.app:run",
                                                     expected_output=0)]})
    with pytest.raises(PermissionError):
        host.check_module_dependencies(spec, {"src/__init__.py": "", "src/app.py": source})


@pytest.mark.parametrize("deny", ["process.run", "file.read"])
async def test_initial_scope_denial_cannot_be_overridden(execution, deny):
    e = execution
    e.rig.task = replace(e.rig.task, delegation_scope=replace(e.rig.task.delegation_scope,
        denied_actions=frozenset({deny})))
    await e.rig.storage.metadata.save_task(e.rig.task)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        result = await run(e, await issue(e))
        assert not result.allowed and not result.approval_id
    e.spawn.assert_not_called()


async def test_package_file_requires_its_own_known_input(execution):
    e = execution
    e.rig.task = replace(e.rig.task, metadata={**e.rig.task.metadata,
        "required_paths": [p for p in e.files if p != "src/__init__.py"]})
    await e.rig.storage.metadata.save_task(e.rig.task)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        assert not (await run(e, await issue(e))).allowed
    e.spawn.assert_not_called()


async def test_unknown_child_outcome_is_not_success_or_automatic_retry(execution, monkeypatch):
    from Sprout.execution.models import ProcessResult
    from Sprout.runtime.nodes import NodeExecutor
    from Sprout.tools.result import ToolExecutionUnconfirmed

    e = execution
    response = AsyncMock(return_value=ProcessResult(command=(), allowed=True, started=True,
                                                   error="controlled unknown"))
    monkeypatch.setattr(e.broker, "run_local_acceptance", response)
    executor = SimpleNamespace(_isolated_coding_required=False, _local_acceptance=True,
                               _process_broker=e.broker)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        with pytest.raises(ToolExecutionUnconfirmed):
            await NodeExecutor._local_acceptance_run(executor, e.rig.workspace, e.rig.task,
                e.root, e.spec, "tests/frozen.py", "normal", local_executor_binding())
    assert response.await_count == 1
    e.spawn.assert_not_called()
