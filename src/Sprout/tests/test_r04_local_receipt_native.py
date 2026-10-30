"""Native Windows Runtime receipt commissioning. No model or Docker execution."""

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from Sprout.execution.acceptance import (
    local_executor_binding,
    render_acceptance_demo,
    render_acceptance_tests,
)
from Sprout.gateway.local_identity import current_cli_principal
from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeBudget, NodeType
from Sprout.orchestration.service import SequentialOrchestrator
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.security.engine import PolicyEngine
from Sprout.session.models import Session
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import DelegationScope, Task
from Sprout.tests.conftest import build_runtime
from Sprout.workspace.models import Workspace, WorkspaceKind

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Explicit Windows native commissioning required; not a guarded unit test",
)


class FixturePolicy(PolicyEngine):
    def decide(self, request):
        base = super().decide(request)
        if request.action is ActionType.PROCESS_RUN and base.decision is not AccessDecision.DENY:
            return PolicyDecision(AccessDecision.ALLOW, "isolated native fixture Host policy")
        return base


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "guard_owner",
        "task_owner",
        "session",
        "workspace",
        "duplicate",
        "missing",
        "failed",
        "unexecuted",
        "executor",
        "during_source",
        "before_issue_source",
        "timeout",
        "pin_check_error",
        "node_task",
        "forged_verified",
    ],
)
async def test_native_runtime_local_receipt(tmp_path, monkeypatch, mode):
    assert os.name == "nt", "This entry requires explicit Windows native execution"
    git = Path("D:/dev/Git/mingw64/bin/git.exe")
    assert hashlib.sha256(git.read_bytes()).hexdigest() == (
        "cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96"
    )
    monkeypatch.setenv("PATH", str(git.parent) + os.pathsep + os.environ.get("PATH", ""))
    from Sprout.sandbox import isolated_git_worktree

    original_capture = isolated_git_worktree.run_capture
    git_calls = []

    async def traced_capture(*args, **kwargs):
        value = await original_capture(*args, **kwargs)
        git_calls.append({"argv": args, "result": value})
        (tmp_path / "GIT_CALLS.json").write_text(json.dumps(git_calls, default=str, indent=2))
        return value

    monkeypatch.setattr(isolated_git_worktree, "run_capture", traced_capture)
    root = tmp_path / "repo"
    root.mkdir()
    spec = AcceptanceSpec.from_payload(
        {"cases": [{"id": "normal", "target": "src.app:echo", "args": [2], "expected_output": 2}]}
    )
    files = {
        "pytest.ini": "[pytest]\naddopts = -p no:cacheprovider\n",
        "src/__init__.py": "",
        "src/app.py": "def echo(value):\n    return value\n",
        "tests/frozen.py": render_acceptance_tests(spec),
        "examples/demo.py": render_acceptance_demo(spec),
    }
    if mode == "during_source":
        files["mutation.py"] = (
            "from pathlib import Path\nPath('src/app.py').write_text('changed')\n"
        )
    if mode == "timeout":
        files["timeout.py"] = "import time\ntime.sleep(5)\n"
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(text.encode())
    env = {k: v for k, v in os.environ.items() if k in {"SystemRoot", "TEMP", "TMP", "PATH"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    base = [
        str(git),
        "-c",
        "core.hooksPath=NUL",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "user.name=Receipt fixture",
        "-c",
        "user.email=receipt@example.invalid",
        "-C",
        str(root),
    ]
    for args in [("init",), ("add", "--", *files), ("commit", "-m", "fixture")]:
        subprocess.run([*base, *args], env=env, capture_output=True, check=True, timeout=15)
    storage = StorageBundle.in_memory()
    storage.metadata = open_metadata_store(tmp_path / "receipt.db")
    runtime, _ = build_runtime(storage=storage)
    runtime._orchestrator_backend = SequentialOrchestrator(storage.metadata)
    runtime._trajectory_dir = tmp_path / "trajectories"
    policy = FixturePolicy()
    runtime._policy_engine = policy
    runtime._file_broker._policy = policy
    runtime._process_broker._policy = policy
    workspace = Workspace(
        id="native-receipt-workspace", root=root, kind=WorkspaceKind.GIT_REPOSITORY
    )
    await storage.metadata.save_workspace(workspace)
    principal = current_cli_principal()
    assert principal.authenticated and principal.source == "cli"
    session = Session(
        id=str(uuid4()),
        user_id=principal.user_id,
        metadata={
            "entry_principal_v1": {
                "user_id": principal.user_id,
                "source": "cli",
                "authenticated": True,
            }
        },
    )
    await runtime._session_store.save_session(session)
    task = Task(
        workspace_id=workspace.id,
        actor=principal,
        source="cli",
        instruction="Validate fixed echo fixture",
        metadata={
            "execution_mode": "code",
            "session_id": session.id,
            "required_paths": list(files),
        },
        delegation_scope=DelegationScope(
            allowed_paths=(str(tmp_path),),
            allowed_actions=frozenset({"tool.invoke", "file.read", "process.run"}),
        ),
    )
    await storage.metadata.save_task(task)
    task = await runtime.bind_task_acceptance(
        task.id,
        principal=principal,
        spec=spec.to_payload(),
        test_path="tests/frozen.py",
        demo_path="examples/demo.py",
    )
    sandbox_node = ExecutionNode(task_id=task.id, type=NodeType.SANDBOX)
    design = ExecutionNode(
        task_id=task.id, type=NodeType.TEST_DESIGN, dependencies=(sandbox_node.id,)
    )
    evaluation = ExecutionNode(
        task_id=task.id,
        type=NodeType.EVALUATION,
        dependencies=(design.id,),
        budget=NodeBudget(timeout_seconds=180),
        metadata={
            "test_commands": (
                ["python -c print(1)", "python -c print(1)"]
                if mode == "duplicate"
                else [""]
                if mode == "missing"
                else ["python missing_file.py"]
                if mode in {"failed", "forged_verified"}
                else ["python mutation.py"]
                if mode == "during_source"
                else []
            )
        },
    )
    graph = ExecutionGraph(task_id=task.id, nodes=(sandbox_node, design, evaluation))
    # Deterministic upstream graph/design, explicitly permitted by commissioning.
    # EVALUATION, ProcessBroker, Guard and receipt issuer remain actual production.
    monkeypatch.setattr(runtime._graph_builder, "build", lambda *a, **kw: graph)
    factory = runtime._executor

    def executor(metadata):
        instance = factory(metadata)

        async def fixed_design(node, ws, current_task):
            await runtime._persist_node_products()  # Previously settled SANDBOX only.
            return {
                "acceptance_spec": spec.to_payload(),
                "test_path": "tests/frozen.py",
                "test_hash": hashlib.sha256(files["tests/frozen.py"].encode()).hexdigest(),
                "demo_path": "examples/demo.py",
                "demo_hash": hashlib.sha256(files["examples/demo.py"].encode()).hexdigest(),
                "binding_sha256": current_task.metadata["host_acceptance_v1"]["sha256"],
                "local_executor": (
                    {**local_executor_binding(), "fingerprint": "0" * 64}
                    if mode == "executor"
                    else local_executor_binding()
                ),
                "local_baseline_valid": True,
            }

        instance._test_design = fixed_design
        return instance

    monkeypatch.setattr(runtime, "_executor", executor)
    observations = []
    held_candidates = []

    class Boundary:
        async def execute(self, flow, **kwargs):
            original_handler = kwargs.pop("handler")

            async def observe(running):
                output = await original_handler(running)
                if running.type is NodeType.EVALUATION:
                    actual = await storage.metadata.get_execution_node(running.id)
                    assert actual.metadata["execution_guard"]["state"] == "active"
                    assert not runtime._local_acceptance_receipts
                    if mode == "forged_verified":
                        output["verified"] = True
                        output["verification_report"]["verified"] = True
                    observations.append("no_receipt_before_guard")
                    sandbox = runtime._frozen_sandboxes[task.id][0]
                    assert (sandbox.root / "src/app.py").read_bytes() == files[
                        "src/app.py"
                    ].encode()
                    if runtime._local_receipt_candidates:
                        held_candidates.extend(runtime._local_receipt_candidates.values())
                        with pytest.raises(PermissionError):
                            (sandbox.root / "src/app.py").write_bytes(b"changed")
                        observations.append("pin_held_before_guard")
                return output

            result = await SequentialOrchestrator(storage.metadata).execute(
                flow, handler=observe, **kwargs
            )
            if flow.task_id == task.id:
                actual = await storage.metadata.get_execution_node(evaluation.id)
                if actual.status.value == "completed":
                    if mode == "guard_owner":
                        # Isolated test-only database fault: retain the real
                        # Guard's completed state, corrupt only its owner.
                        await storage.metadata._db.execute(
                            "UPDATE execution_nodes SET metadata_json=json_set("
                            "metadata_json,'$.execution_guard.owner','fault-owner') WHERE id=?",
                            (actual.id,),
                        )
                    elif mode == "node_task":
                        other = Task(workspace_id=workspace.id, actor=principal, source="cli")
                        await storage.metadata.save_task(other)
                        await storage.metadata._db.execute(
                            "UPDATE execution_nodes SET task_id=? WHERE id=?", (other.id, actual.id)
                        )
                    elif mode == "task_owner":
                        current = await storage.metadata.get_task(task.id)
                        await storage.metadata.save_task(
                            replace(current, actor=replace(principal, user_id="other"))
                        )
                    elif mode == "session":
                        await runtime._session_store.save_session(replace(session, user_id="other"))
                    elif mode == "workspace":
                        await storage.metadata.save_workspace(
                            replace(workspace, revision="changed")
                        )
                    elif mode == "pin_check_error":
                        candidate = next(iter(runtime._local_receipt_candidates.values()))

                        def fail_pin_check():
                            raise PermissionError("controlled native-pin check fault")

                        monkeypatch.setattr(candidate["pin"], "check", fail_pin_check)
                    elif mode == "before_issue_source":
                        sandbox = runtime._frozen_sandboxes[task.id][0]
                        with pytest.raises(PermissionError):
                            (sandbox.root / "src/app.py").write_bytes(b"changed")
                        replacement = sandbox.root / "replacement.tmp"
                        replacement.write_bytes(b"changed")
                        with pytest.raises(PermissionError):
                            os.replace(replacement, sandbox.root / "src/app.py")
                        replacement.unlink()
                        observations.append("pin_prevented_post_guard_write")
                        observations.append("pin_prevented_post_guard_replace")
            return result

    runtime._orchestrator_backend = Boundary()
    if mode == "timeout":
        runtime._process_broker._default_timeout = 0.2
    if mode == "unexecuted":

        class DenyProcess(FixturePolicy):
            def decide(self, request):
                if request.action is ActionType.PROCESS_RUN:
                    return PolicyDecision(AccessDecision.DENY, "fixture denial")
                return super().decide(request)

        runtime._policy_engine = DenyProcess()
        runtime._file_broker._policy = runtime._policy_engine
        runtime._process_broker._policy = runtime._policy_engine
    try:
        result = await runtime._execute_task(task, artifact_snapshot=None)
        nodes = await storage.metadata.list_execution_nodes(task.id)
        evidence = {
            "result": str(result),
            "nodes": [
                {"type": n.type.value, "status": n.status.value, "metadata": n.metadata}
                for n in nodes
            ],
        }
        (tmp_path / "EVIDENCE.json").write_text(json.dumps(evidence, default=str, indent=2))
        if mode not in {"success", "before_issue_source"}:
            assert not runtime._local_acceptance_receipts, evidence
            assert not runtime._local_receipt_candidates
            for candidate in held_candidates:
                assert not candidate["stack"]._exit_callbacks
            if mode == "during_source":
                assert (root / "src/app.py").read_bytes() == files["src/app.py"].encode()
            return
        assert runtime._local_acceptance_receipts, evidence
        assert "no_receipt_before_guard" in observations
        receipt, (payload, candidate) = next(iter(runtime._local_acceptance_receipts.items()))
        assert not candidate["stack"]._exit_callbacks  # Released only after real issuance.
        assert "pin_held_before_guard" in observations
        assert receipt.task_id == task.id and receipt.node_id == evaluation.id
        assert payload["before"] == payload["after"]
        assert payload["guard_state"] == "completed" and payload["node_status"] == "completed"
        from copy import copy

        assert copy(receipt) not in runtime._local_acceptance_receipts
        (tmp_path / "REGISTRY.json").write_text(
            json.dumps(
                {
                    "issuer_is_runtime": receipt.issuer is runtime,
                    "registered": receipt in runtime._local_acceptance_receipts,
                    "task_id": receipt.task_id,
                    "node_id": receipt.node_id,
                    "owner": receipt.owner,
                    "payload_sha256": receipt.payload_sha256,
                    "pins_released_after_issuance": not candidate["stack"]._exit_callbacks,
                    "observations": observations,
                },
                indent=2,
            )
        )
        (tmp_path / "RECEIPT.json").write_text(json.dumps(payload, default=str, indent=2))
    finally:
        await runtime.stop()
        assert not runtime._local_acceptance_receipts
        registered = runtime._frozen_sandboxes.get(task.id)
        lifecycle = {"registry_revoked": True, "scope": "this isolated test Runtime"}
        if registered:
            source = registered[0].root / "src/app.py"
            if source.exists():
                # Request real WRITE access without truncating or changing data.
                # A leaked strong READ pin would cause a sharing violation.
                with source.open("r+b") as stream:
                    assert stream.read() == files["src/app.py"].encode()
                lifecycle["post_stop_write_handle_opened_without_mutation"] = True
            else:
                lifecycle["source_removed_by_normal_fixture_lifecycle"] = True
        (tmp_path / "PIN_LIFECYCLE.json").write_text(json.dumps(lifecycle, indent=2))
