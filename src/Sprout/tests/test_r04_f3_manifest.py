"""F3 manifest/export REDs; never execute Docker or target Python."""
import hashlib
import json
import os
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from Sprout.execution.file_snapshot import ResourceIdentity
from Sprout.tests.test_r04_f2_execution_contract import execution as execution
from Sprout.tests.test_r04_f2_execution_contract import rig as rig
from Sprout.workspace.authorized_content import AuthorizedContentView, AuthorizedEntry
from Sprout.workspace.models import ResourceKind


def view():
    data = b"def run():\r\n    return 1\r\n"
    return AuthorizedContentView("workspace", (AuthorizedEntry("src/app.py", ResourceKind.SOURCE,
        data, hashlib.sha256(data).hexdigest(), ResourceIdentity(1, 2, len(data), 3, 4)),))


def test_canonical_manifest_is_data_not_authority():
    from Sprout.execution.verification_manifest import VerificationManifestV1, ordinary_obligations
    from Sprout.strategy.acceptance import AcceptanceSpec

    host = {"paths": ("src/app.py", "tests/frozen.py", "examples/demo.py"),
            "images": {"verification": "sha256:" + "a" * 64, "coding": "sha256:" + "b" * 64},
            "tools": {}, "discovery": None}
    node = SimpleNamespace(metadata={"test_commands": ["pytest"], "verification_commands": [
        {"command": "pytest -q", "required": True}]})
    spec = AcceptanceSpec.from_payload({"cases": [dict(id="case", target="app:run",
                                                     expected_output=1)]})
    obligations = ordinary_obligations(host, node, spec,
        {"test_path": "tests/frozen.py", "demo_path": "examples/demo.py"}, ["src/app.py"])
    payload = dict(version=1, task="task", workspace="workspace", node="node", owner="owner",
        principal={"authenticated": True, "source": "cli", "user_id": "host"}, source="cli",
        scope={}, sandbox={}, worktree_identity=["unit", 1, 2], acceptance={}, diff="a" * 64,
        inputs={name: {"digest": "a" * 64, "identity": {"device": 1, "inode": 2,
                "size": 1, "mtime_ns": 3, "ctime_ns": 4}, "size": 1, "kind": "source"}
                for name in host["paths"]}, obligations=obligations,
        budgets={}, approval={}, host_requirements="b" * 64, export={})
    manifest = VerificationManifestV1.from_payload(payload)
    assert manifest.version == 1
    copied = VerificationManifestV1.from_payload(json.loads(manifest.canonical_json))
    assert copied is not manifest and copied.sha256 == manifest.sha256
    payload["task"] = "changed"
    assert manifest.to_payload()["task"] == "task"
    assert len(obligations) == 6
    assert obligations[0]["argv"] == ["pytest"]
    assert obligations[1]["argv"] == ["pytest", "-q"]
    assert obligations[4]["argv"] == ["python", "examples/demo.py"]
    assert all(item["required"] for item in obligations)
    with pytest.raises(ValueError):
        VerificationManifestV1.from_payload({"task_id": "task"})


def test_exact_export_requires_live_runtime_receipt(tmp_path):
    from Sprout.execution.verification_export import export_authorized_view

    with pytest.raises(PermissionError):
        export_authorized_view(view(), tmp_path, receipt=None)
    assert not list(tmp_path.iterdir())


def test_synthetic_view_does_not_grant_export(tmp_path):
    from Sprout.execution.verification_export import export_authorized_view

    synthetic = replace(view(), entries=(AuthorizedEntry.synthetic("src/app.py", b"pass\n"),))
    with pytest.raises(PermissionError):
        export_authorized_view(synthetic, tmp_path, receipt={"trusted": True})
    assert not list(tmp_path.iterdir())


@pytest.fixture
async def f3(execution, monkeypatch):
    from Sprout.execution.file_snapshot import read_fd
    from Sprout.orchestration.models import ExecutionNode, NodeBudget, NodeStatus, NodeType

    e = execution
    runtime = e.rig.runtime
    parent = e.root.parent / "f3-exports"
    parent.mkdir()
    from Sprout.execution.host_acceptance import pin_execution_directory

    with pin_execution_directory(parent) as parent_pin:
        parent_identity = parent_pin.identity
    oracles = {}
    for path in e.frozen["resources"]:
        with (e.root / path).open("rb") as stream:
            from dataclasses import asdict

            oracles[path] = {"digest": hashlib.sha256(stream.read()).hexdigest(),
                "identity": asdict(ResourceIdentity.from_stat(os.fstat(stream.fileno())))}
    # Explicit unit Host assembly, not a production grant installation path.
    runtime._verification_hosts[e.rig.task.id] = {
        "task": deepcopy(e.rig.task), "workspace": deepcopy(e.rig.workspace),
        "paths": tuple(e.files), "parent": parent,
        "images": {"verification": "sha256:" + "a" * 64, "coding": "sha256:" + "b" * 64},
        "discovery": None, "tools": {}, "oracles": oracles,
        "source_digests": {path: hashlib.sha256((e.rig.root / path).read_bytes()).hexdigest()
                           for path in e.files},
        "parent_identity": parent_identity,
    }
    pending = ExecutionNode(id="f3-agent", task_id=e.rig.task.id, type=NodeType.AGENT)
    await e.rig.storage.metadata.save_execution_node(pending)
    assert await e.rig.commission(pending, "f3-agent-owner", "")
    running = await e.rig.storage.metadata.get_execution_node(pending.id)
    with runtime._project_analysis_scope(e.rig.task, e.rig.workspace, running) as scope:
        runtime._node_publications[running.id] = scope
        runtime._remember_node_product(scope, {"content": "controlled unit model result"})
    assert await e.rig.storage.metadata.finish_execution_node(replace(running,
        status=NodeStatus.COMPLETED, metadata={**running.metadata,
            "output": {"content": "controlled unit model result"}}), "f3-agent-owner", "completed")
    await runtime._persist_node_products()
    pending = ExecutionNode(id="f3-evaluation", task_id=e.rig.task.id, type=NodeType.EVALUATION,
        budget=NodeBudget(timeout_seconds=300),
        dependencies=(running.id,), metadata={"test_commands": ["pytest"],
            "verification_commands": [{"command": "pytest -q", "required": True}]})
    await e.rig.storage.metadata.save_execution_node(pending)
    assert await e.rig.commission(pending, "f3-owner", "")
    e.rig.node = await e.rig.storage.metadata.get_execution_node(pending.id)

    def operation(root, target, operation, authorize, *, content, **kwargs):
        # Controlled tmp-file OS substitute only. Production always uses R03 primitives.
        assert root in {e.root, e.rig.root, parent} or root.is_relative_to(parent)
        authorize()
        if operation == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
            return True
        assert operation == "snapshot"
        with target.open("rb") as stream:
            return read_fd(stream.fileno(), content)
    monkeypatch.setattr("Sprout.execution.file_broker.file_operation", operation)
    monkeypatch.setattr("Sprout.execution.file_access.file_operation", operation)
    async def listing(root, target, limit, authorize):
        # Unit name/identity substitute, never a native enumeration claim.
        from Sprout.execution.file_snapshot import DirectoryEntry

        items = []
        for path in target.iterdir():
            if not await authorize(path):
                continue
            if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
                raise PermissionError("unit_link_denied")
            items.append(DirectoryEntry(path.relative_to(root).as_posix(), path.is_dir(),
                ResourceIdentity.from_stat(path.stat())))
        assert len(items) <= limit
        return tuple(items)
    monkeypatch.setattr("Sprout.execution.file_access.directory_entries", listing)
    (e.root / "src/app.py").write_bytes(b"def summarize(x):\r\n    return {'changed': 1}\r\n")
    return e


async def test_new_evaluation_prepares_exact_modified_view_without_execution(f3):
    e = f3
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        result = await e.rig.runtime._evaluate_host_verification(e.rig.node)
    assert result["verified"] is False
    assert result["verification_report"]["verified"] is False
    assert len(result["results"]) == 6  # five obligations + fixture's one frozen case
    assert all(r["exit_code"] is None and not r["passed"] for r in result["results"])
    root = __import__("pathlib").Path(result["export"]["root"])
    assert (root / "src/app.py").read_bytes() == (e.root / "src/app.py").read_bytes()
    assert b"\r\n" in (root / "src/app.py").read_bytes()
    assert (root / "src/app.py").read_bytes() != (e.rig.root / "src/app.py").read_bytes()
    assert "changed" in result["code_diff"]
    assert not e.rig.runtime._verification_frames
    assert not e.rig.runtime._verification_preparations
    e.spawn.assert_not_called()


@pytest.mark.parametrize("change", ["extra", "content", "replacement", "alias"])
async def test_prepare_receipt_rejects_before_unselected_content_read(f3, monkeypatch, change):
    from pathlib import Path

    e = f3
    original = e.broker.prepare_verification
    reads = []
    read_bytes = Path.read_bytes
    async def intercepted(manifest, issuer, scope, exported):
        root = Path(exported["root"])
        target = root / "src/app.py"
        extra = root / "unselected.py"
        if change == "extra":
            extra.write_bytes(b"# not selected\n")
        elif change == "alias":
            os.link(target, extra)
        elif change == "replacement":
            data = target.read_bytes()
            target.unlink()
            target.write_bytes(data)
        else:
            target.write_bytes(b"# altered selected input\n")
        def observed(path):
            if path == extra:
                reads.append(path)
            return read_bytes(path)
        monkeypatch.setattr(Path, "read_bytes", observed)
        return await original(manifest, issuer, scope, exported)
    monkeypatch.setattr(e.broker, "prepare_verification", intercepted)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        with pytest.raises((PermissionError, OSError)):
            await e.rig.runtime._evaluate_host_verification(e.rig.node)
    assert not reads
    assert not e.rig.runtime._verification_preparations
    e.spawn.assert_not_called()


@pytest.mark.parametrize("change", ["copy-only", "envelope", "policy", "scope", "tool", "combined"])
async def test_compile_copy_never_clears_independent_blockers(f3, change):
    from Sprout.orchestration.models import ExecutionNode, NodeBudget, NodeType
    from Sprout.security.access import AccessDecision

    e = f3
    host = e.rig.runtime._verification_hosts[e.rig.task.id]
    if change != "tool":
        host["tools"] = {"python": {"declared": "unit-tool"}}
    metadata = {"test_commands": ["pytest"],
                "verification_commands": [{"command": "pytest -q", "required": True}]}
    if change in {"envelope", "combined"}:
        metadata["build_commands"] = ["unapproved-build"]
    pending = ExecutionNode(id="blocker-evaluation", task_id=e.rig.task.id,
        type=NodeType.EVALUATION, dependencies=e.rig.node.dependencies,
        budget=NodeBudget(timeout_seconds=300), metadata=metadata)
    await e.rig.storage.metadata.save_execution_node(pending)
    assert await e.rig.commission(pending, "blocker-owner", "")
    node = await e.rig.storage.metadata.get_execution_node(pending.id)
    if change in {"policy", "combined"}:
        e.mode[0] = AccessDecision.DENY
    if change == "scope":
        # Resource reads remain allowed; only execution is denied.
        task = replace(e.rig.task, delegation_scope=replace(e.rig.task.delegation_scope,
            denied_actions=frozenset({"process.run"})))
        await e.rig.storage.metadata.save_task(task)
        e.rig.task = task
        host["task"] = task
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, node):
        result = await e.rig.runtime._evaluate_host_verification(node)
        row = next(r for r in result["results"] if r["kind"] == "compileall")
        assert not row["passed"] and row["exit_code"] is None
        if change == "envelope":
            assert row["error"] == "verification_command_envelope_changed"
        elif change in {"policy", "combined", "scope"}:
            assert row["outcome"] == "policy_denied"
        elif change == "tool":
            assert row["error"] == "verification_tool_identity_unproved"
        else:
            assert row["error"] == "verification_native_commissioning_required"


@pytest.mark.parametrize("change", ["inside", "ancestor", "workspace", "parent-replaced", "alias",
                                   "database"])
async def test_export_boundary_rejects_without_workspace_writes(f3, monkeypatch, change):
    from Sprout.execution import file_access
    from Sprout.execution.host_acceptance import pin_execution_directory

    e = f3
    writes = []
    operation = file_access.file_operation
    def observed(root, target, action, authorize, **kwargs):
        if action == "write":
            writes.append(target)
        return operation(root, target, action, authorize, **kwargs)
    monkeypatch.setattr(file_access, "file_operation", observed)
    host = e.rig.runtime._verification_hosts[e.rig.task.id]
    before = {p: (e.root / p).read_bytes() for p in e.files}
    if change == "inside":
        parent = e.root / "src"
    elif change == "ancestor":
        parent = e.root.parent
    elif change == "workspace":
        parent = e.rig.root
    else:
        parent = host["parent"]
    with pin_execution_directory(parent) as pin:
        host["parent_identity"] = pin.identity
    host["parent"] = parent
    if change == "parent-replaced":
        parent.rename(parent.with_name("old-export-parent"))
        parent.mkdir()
    if change == "alias":
        from Sprout.execution import docker_verification

        safe = docker_verification.safe_path
        monkeypatch.setattr(docker_verification, "safe_path",
            lambda path: e.root if path == parent else safe(path))
    if change == "database":
        from Sprout.config.settings import Settings, StorageSettings

        e.rig.runtime._settings = Settings(storage=StorageSettings(
            metadata="sqlite:///" + (parent / "evidence.db").as_posix()))
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        with pytest.raises(PermissionError):
            await e.rig.runtime._evaluate_host_verification(e.rig.node)
    assert before == {p: (e.root / p).read_bytes() for p in e.files}
    assert not writes
    e.spawn.assert_not_called()


async def test_compileall_preparation_uses_separate_disposable_copy(f3):
    e = f3
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        result = await e.rig.runtime._evaluate_host_verification(e.rig.node)
    compile_row = next(r for r in result["results"] if r["kind"] == "compileall")
    assert compile_row["preparation"] is not None
    command = compile_row["preparation"]["command"]
    assert compile_row["preparation"]["native_start_allowed"] is False
    assert compile_row["command"] == ["python", "-m", "compileall", "-q", "src/app.py"]
    copy = result["export"]["compileall"]
    assert copy["root"] != result["export"]["root"]
    assert set(copy["files"]) == {"src/app.py"}
    assert "--read-only" in command and "--network=none" in command
    assert f"type=bind,src={copy['root']},dst=/workspace" in command
    e.spawn.assert_not_called()


@pytest.mark.parametrize("change", ["missing-frame", "scope", "owner", "workspace", "frozen",
    "other-worktree", "budget", "expiry", "path-escape"])
async def test_snapshot_invalid_context_returns_no_bytes(f3, monkeypatch, change):
    from datetime import UTC, datetime, timedelta

    e = f3
    broker = e.rig.runtime._file_broker
    if change == "missing-frame":
        result = await broker.read_worktree_snapshot(e.sandbox, "src/app.py")
        assert not result.ok and result.data == b""
        return
    if change == "scope":
        await e.rig.storage.metadata.save_task(replace(e.rig.task, delegation_scope=replace(
            e.rig.task.delegation_scope, denied_actions=frozenset({"file.read"}))))
    if change == "expiry":
        await e.rig.storage.metadata.save_task(replace(e.rig.task, delegation_scope=replace(
            e.rig.task.delegation_scope, expires_at=datetime.now(UTC) - timedelta(seconds=1))))
    if change == "workspace":
        await e.rig.storage.metadata.save_task(replace(e.rig.task, workspace_id="wrong"))
    if change == "other-worktree":
        registered = e.rig.runtime._frozen_sandboxes[e.rig.task.id]
        e.rig.runtime._frozen_sandboxes[e.rig.task.id] = (replace(e.sandbox, root=e.rig.root),
                                                                      *registered[1:])
    if change == "owner":
        original = e.rig.storage.metadata.get_execution_node

        async def read(node_id):
            node = await original(node_id)
            if node.id == e.rig.node.id:
                return replace(node, metadata={**node.metadata, "execution_guard": {
                    **node.metadata["execution_guard"], "owner": "wrong"}})
            return node
        monkeypatch.setattr(e.rig.storage.metadata, "get_execution_node", read)
    if change == "frozen":
        (e.root / "tests/frozen.py").write_text("# altered\n", encoding="utf-8")
    if change == "budget":
        (e.root / "src/app.py").write_bytes(b"x" * 100_001)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        with pytest.raises((PermissionError, KeyError)):
            if change == "path-escape":
                await e.rig.runtime._capture_verification_view(
                    __import__("Sprout.tools.context_keys", fromlist=["_current_project_node"])
                    ._current_project_node(), ("../outside",))
            else:
                await e.rig.runtime._evaluate_host_verification(e.rig.node)
    assert not list(e.rig.runtime._verification_hosts[e.rig.task.id]["parent"].iterdir())
    e.spawn.assert_not_called()


@pytest.mark.parametrize("change", ["valid", "principal", "scope", "running", "image",
                                   "overlap", "missing-workspace"])
async def test_explicit_host_registration_is_limited_to_new_tasks(f3, change):
    from Sprout.execution.host_acceptance import binding_subject
    from Sprout.execution.node_result_source import payload_digest
    from Sprout.task.models import TaskStatus

    e = f3
    runtime = e.rig.runtime
    task = replace(e.rig.task, id="new-host-task", status=TaskStatus.CREATED)
    if change == "running":
        task = replace(task, status=TaskStatus.RUNNING)
    if change == "scope":
        task = replace(task, delegation_scope=replace(task.delegation_scope,
                       denied_actions=frozenset({"file.read"})))
    if change == "missing-workspace":
        task = replace(task, workspace_id="missing")
    frozen = deepcopy(e.frozen)
    frozen["subject"] = binding_subject(task, e.rig.workspace)
    frozen["sha256"] = payload_digest({k: v for k, v in frozen.items() if k != "sha256"})
    task = replace(task, metadata={**task.metadata, "host_acceptance_v1": frozen})
    await e.rig.storage.metadata.save_task(task)
    actor = task.actor if change != "principal" else replace(task.actor, authenticated=False)
    images = {"verification": "sha256:" + "a" * 64, "coding": "sha256:" + "b" * 64}
    if change == "image":
        images["verification"] = "mutable:latest"
    parent = e.rig.root if change == "overlap" else e.root.parent / "f3-exports"

    async def bind():
        return await runtime.bind_task_verification(task.id, actor, paths=tuple(e.files),
            export_parent=parent, images=images,
            source_digests={path: hashlib.sha256((e.rig.root / path).read_bytes()).hexdigest()
                            for path in e.files})
    if change == "valid":
        await bind()
        images["verification"] = "modified-after-binding"
        assert runtime._verification_hosts[task.id]["images"]["verification"].startswith("sha256:")
        assert await e.rig.storage.metadata.get_task(task.id) == task
        with pytest.raises(PermissionError):
            await bind()
    else:
        with pytest.raises((PermissionError, ValueError)):
            await bind()
        assert task.id not in runtime._verification_hosts
    e.spawn.assert_not_called()


@pytest.mark.parametrize("change", ["copy", "task", "principal", "owner", "image", "command"])
async def test_manifest_copy_cannot_authorize_preflight(f3, change):
    from Sprout.execution.verification_manifest import VerificationManifestV1

    e = f3
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node) as scope:
        result = await e.rig.runtime._evaluate_host_verification(e.rig.node)
        payload = json.loads(json.dumps(result["manifest"]))
        if change in {"task", "owner"}:
            payload[change] = "wrong"
        if change == "principal":
            payload["principal"]["user_id"] = "other"
        if change == "image":
            payload["obligations"][0]["image"] = "sha256:" + "c" * 64
        if change == "command":
            payload["obligations"][0]["argv"] = ["sh", "-c", "not-authorized"]
        copied = VerificationManifestV1.from_payload(payload)
        with pytest.raises(PermissionError, match="preparation_unavailable"):
            await e.broker.prepare_verification(copied, e.rig.runtime, scope, result["export"])
    e.spawn.assert_not_called()


async def test_preflight_policy_denial_does_not_touch_approval(f3, monkeypatch):
    from unittest.mock import AsyncMock

    from Sprout.security.access import AccessDecision

    e = f3
    e.mode[0] = AccessDecision.DENY
    request = AsyncMock(side_effect=AssertionError("Approval requested"))
    query = AsyncMock(side_effect=AssertionError("Approval consumed"))
    monkeypatch.setattr(e.broker._approvals, "request", request)
    monkeypatch.setattr(e.broker._approvals, "is_approved", query)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        result = await e.rig.runtime._evaluate_host_verification(e.rig.node)
    assert any(r["outcome"] == "policy_denied" for r in result["results"])
    assert result["verified"] is False
    request.assert_not_called()
    query.assert_not_called()
    e.spawn.assert_not_called()


@pytest.mark.parametrize("change", ["expired", "child-task", "replaced-file"])
async def test_capture_frame_expiry_copy_and_replacement(f3, monkeypatch, change):
    import asyncio

    from Sprout.execution.verification_manifest import current_frame

    e = f3
    original = e.rig.runtime._file_broker.read_worktree_snapshot

    async def capture(sandbox, path, **kwargs):
        if change == "expired":
            current_frame().expires = 0
        if change == "child-task":
            denied = await asyncio.create_task(original(sandbox, path, **kwargs))
            assert not denied.ok and not denied.data
        if change == "replaced-file":
            frame = current_frame()
            # The root/leaf pin was captured before worker execution.
            frame.issuer._verification_frames[frame]["pin"].pin_file(path)
            (sandbox.root / path).write_bytes(b"# replaced after pin\n")
        return await original(sandbox, path, **kwargs)
    monkeypatch.setattr(e.rig.runtime._file_broker, "read_worktree_snapshot", capture)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node) as scope:
        if change == "child-task":
            entries, _, _ = await e.rig.runtime._capture_verification_view(scope, ("src/app.py",))
            assert len(entries) == 1
        else:
            with pytest.raises(PermissionError):
                await e.rig.runtime._capture_verification_view(scope, ("src/app.py",))
    assert not e.rig.runtime._verification_frames
    e.spawn.assert_not_called()


async def test_cancellation_joins_snapshot_and_publishes_no_export(f3, monkeypatch):
    import asyncio
    import time

    e = f3
    module = __import__("Sprout.execution.file_broker", fromlist=["file_operation"])
    original = module.file_operation
    ended = []
    loop, current = asyncio.get_running_loop(), asyncio.current_task()

    def slow(*args, **kwargs):
        loop.call_soon_threadsafe(current.cancel)
        time.sleep(0.04)
        try:
            return original(*args, **kwargs)
        finally:
            ended.append(True)
    monkeypatch.setattr("Sprout.execution.file_broker.file_operation", slow)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node) as scope:
        with pytest.raises(asyncio.CancelledError):
            await e.rig.runtime._capture_verification_view(scope, ("src/app.py",), exporting=True)
    assert ended and not e.rig.runtime._verification_frames
    assert not list(e.rig.runtime._verification_hosts[e.rig.task.id]["parent"].iterdir())
    e.spawn.assert_not_called()


@pytest.mark.parametrize("change", ["valid", "running", "wrong-id", "wrong-owner", "image"])
def test_owned_container_inspection_never_infers_cli_exit(tmp_path, change):
    from Sprout.execution.verification_backend import VerificationRequest
    from Sprout.execution.verification_run import DockerVerificationRun

    original, exported = tmp_path / "original", tmp_path / "export"
    original.mkdir()
    exported.mkdir()
    (exported / "app.py").write_text("pass\n", encoding="utf-8")
    observer = tmp_path / "observer.py"
    observer.write_text("# fixture observer, never executed\n", encoding="utf-8")
    plan = DockerVerificationRun.prepare(VerificationRequest("task", original, exported,
        observer, "sha256:" + "a" * 64, ("-", "nonce")), node_id="node", owner="owner",
        manifest_sha="c" * 64)
    assert plan["native_start_allowed"] is False
    payload = {"Id": "d" * 64, "Name": "/" + plan["container_name"], "Image": plan["image"],
        "Config": {"Labels": {"sprout.role": "independent-verification",
            **{"sprout.verification." + k: plan[v] for k, v in
               (("task", "task_id"), ("node", "node_id"), ("owner", "owner"),
                ("manifest", "manifest"))}}},
        "State": {"Running": False, "Pid": 0, "Status": "exited", "ExitCode": 0}}
    if change == "running":
        payload["State"]["Running"] = True
    if change == "wrong-owner":
        payload["Config"]["Labels"]["sprout.verification.owner"] = "other"
    if change == "image":
        payload["Image"] = "sha256:" + "f" * 64
    if change in {"wrong-id", "wrong-owner", "image"}:
        with pytest.raises(ValueError):
            DockerVerificationRun.observed_container(plan, payload,
                expected_id="e" * 64 if change == "wrong-id" else "d" * 64)
    else:
        result = DockerVerificationRun.observed_container(plan, payload, expected_id="d" * 64)
        assert result["stopped_observed"] is (change == "valid")
        assert result["verified"] is False and result["retry_allowed"] is False


async def untracked_completion(e):
    from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType

    pending = ExecutionNode(id="f3-untracked-agent", task_id=e.rig.task.id, type=NodeType.AGENT)
    await e.rig.storage.metadata.save_execution_node(pending)
    assert await e.rig.commission(pending, "actual-agent-owner", "")
    running = await e.rig.storage.metadata.get_execution_node(pending.id)
    output = {"content": "not certified as resource-derived knowledge"}
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, running) as scope:
        scope.untracked_tool_use = True
        e.rig.runtime._node_publications[running.id] = scope
        e.rig.runtime._remember_node_product(scope, output)
    assert await e.rig.storage.metadata.finish_execution_node(replace(running,
        status=NodeStatus.COMPLETED, metadata={**running.metadata, "output": output}),
        "actual-agent-owner", "completed")
    await e.rig.runtime._persist_node_products()
    assert await e.rig.storage.metadata.get_node_result_source(running.id) is None
    return await e.rig.storage.metadata.get_execution_node(running.id)


@pytest.mark.parametrize("change", ["valid", "owner", "output", "running", "uncertain",
    "failed", "cancelled", "task", "forged", "cross-runtime", "scope", "stop"])
async def test_completion_link_is_not_a_resource_or_publication_grant(f3, monkeypatch, change):
    from datetime import UTC, datetime, timedelta

    from Sprout.orchestration.models import NodeStatus

    e = f3
    parent = await untracked_completion(e)
    candidates = e.rig.runtime._verification_hosts[e.rig.task.id]["agent_candidates"]
    if change == "forged":
        candidates[parent.id] = {"owner": "actual-agent-owner", "trusted": True}
    if change == "cross-runtime":
        candidates[parent.id] = replace(candidates[parent.id], issuer=object())
    if change == "scope":
        await e.rig.storage.metadata.save_task(replace(e.rig.task, delegation_scope=replace(
            e.rig.task.delegation_scope, expires_at=datetime.now(UTC) - timedelta(seconds=1))))
    if change == "stop":
        await e.rig.runtime.stop()
    original = e.rig.storage.metadata.get_execution_node

    async def authoritative(node_id):
        node = await original(node_id)
        if node_id == parent.id:
            if change == "owner":
                return replace(node, metadata={**node.metadata, "execution_guard": {
                    **node.metadata["execution_guard"], "owner": "other"}})
            if change == "output":
                return replace(node, metadata={**node.metadata, "output": {"content": "changed"}})
            if change in {"running", "failed", "cancelled"}:
                return replace(node, status=NodeStatus(change))
            if change == "uncertain":
                return replace(node, status=NodeStatus.WAITING, metadata={**node.metadata,
                    "execution_guard": {**node.metadata["execution_guard"], "state": "uncertain"}})
            if change == "task":
                return replace(node, task_id="other-task")
        return node
    monkeypatch.setattr(e.rig.storage.metadata, "get_execution_node", authoritative)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node) as scope:
        if change == "valid":
            assert await e.rig.runtime._accept_verification_agent_completion(scope, parent) is True
            # Completion confers no capture authority outside the separate F3 frame.
            read = await e.rig.runtime._file_broker.read_worktree_snapshot(e.sandbox, "src/app.py")
            assert not read.ok and read.data == b""
        else:
            with pytest.raises(PermissionError):
                await e.rig.runtime._accept_verification_agent_completion(scope, parent)
    assert await e.rig.storage.metadata.get_node_result_source(parent.id) is None
    e.spawn.assert_not_called()


async def test_untracked_completion_can_prepare_only_after_independent_capture(f3):
    from Sprout.orchestration.models import ExecutionNode, NodeType

    e = f3
    parent = await untracked_completion(e)
    pending = ExecutionNode(id="f3-completion-evaluation", task_id=e.rig.task.id,
        type=NodeType.EVALUATION, dependencies=(parent.id,), metadata=e.rig.node.metadata.copy())
    pending = replace(pending, metadata={k: v for k, v in pending.metadata.items()
                                        if k != "execution_guard"})
    await e.rig.storage.metadata.save_execution_node(pending)
    assert await e.rig.commission(pending, "actual-evaluation-owner", "")
    node = await e.rig.storage.metadata.get_execution_node(pending.id)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, node):
        result = await e.rig.runtime._evaluate_host_verification(node)
    assert result["verified"] is False and result["manifest"]["node"] == node.id
    assert result["manifest"]["inputs"]["src/app.py"]["digest"] == hashlib.sha256(
        (e.root / "src/app.py").read_bytes()).hexdigest()
    assert await e.rig.storage.metadata.get_node_result_source(parent.id) is None
    e.spawn.assert_not_called()


@pytest.mark.parametrize("decision", ["approve", "reject", "scope", "policy", "fingerprint"])
async def test_real_manager_decision_reissues_key_without_consuming_or_launching(
    f3, monkeypatch, decision
):
    import asyncio
    import time

    from Sprout.security.access import AccessDecision
    from Sprout.security.approval import ApprovalStatus

    e = f3
    original = e.broker.prepare_verification
    approved_ids = []

    async def prepare(manifest, issuer, scope, exported):
        await original(manifest, issuer, scope, exported)
        arguments = e.broker.verification_approval_arguments(manifest, issuer)
        manager = e.broker._approvals
        approval = await manager.request("process_run", arguments, task_id=e.rig.task.id,
            source=e.rig.principal.source, requested_by=e.rig.principal.user_id,
            resource_scope=e.rig.workspace.id, session_id=scope.session_id)
        approved_ids.append(approval.id)
        issuer._verification_preparations[manifest]["expires"] = 0
        with pytest.raises(PermissionError):
            await original(manifest, issuer, scope, exported)  # expired key cannot prepare

        async def human():
            await asyncio.sleep(0.03)
            if decision == "scope":
                await e.rig.storage.metadata.save_task(replace(e.rig.task, delegation_scope=replace(
                    e.rig.task.delegation_scope, denied_actions=frozenset({"file.read"}))))
            if decision == "policy":
                e.mode[0] = AccessDecision.DENY
            await manager.decide(approval.id, decision != "reject", decided_by="unit-human")
            if decision == "fingerprint":
                stored = await manager.store.get_approval(approval.id)
                await manager.store.save_approval(replace(stored, arguments_fingerprint="wrong"))
        helper = asyncio.create_task(human())
        try:
            fresh = await e.broker.await_verification_approval(
                manifest, issuer, scope, exported, approval.id)
        finally:
            await helper
        assert fresh is not manifest and fresh.sha256 == manifest.sha256
        assert manifest not in issuer._verification_preparations
        assert issuer._verification_preparations[fresh]["expires"] > time.monotonic()
        final = await original(fresh, issuer, scope, exported)
        assert (await manager.store.get_approval(approval.id)).status is ApprovalStatus.APPROVED
        if decision == "policy":
            assert any(r["outcome"] == "policy_denied" for r in final.values())
        return final
    monkeypatch.setattr(e.broker, "prepare_verification", prepare)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        if decision in {"reject", "scope", "fingerprint"}:
            with pytest.raises(PermissionError):
                await e.rig.runtime._evaluate_host_verification(e.rig.node)
        else:
            result = await e.rig.runtime._evaluate_host_verification(e.rig.node)
            assert result["verified"] is False
    assert len(approved_ids) == 1 and not e.rig.runtime._verification_preparations
    e.spawn.assert_not_called()
