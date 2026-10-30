"""Explicit Host-owned integration entry only; not default pytest discovery.

Real Runtime/SQLite/Guard/Proposal and real R03/Git. Model/verification command
execution is substituted; no application Python or Docker runs here.
"""

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from Sprout.execution.acceptance import render_acceptance_demo, render_acceptance_tests
from Sprout.execution.models import SandboxRef
from Sprout.gateway.identity import Principal
from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeBudget, NodeType
from Sprout.orchestration.service import SequentialOrchestrator
from Sprout.session.models import Session
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import DelegationScope
from Sprout.tests.conftest import build_runtime
from Sprout.tests.test_r04_f3_claim import record
from Sprout.workspace.models import ReadPlan

GIT = Path("D:/dev/Git/mingw64/bin/git.exe")
GIT_SHA = "cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96"
CHAIN = (
    ("D:/dev/Git/cmd/git.exe", "81ef35ae005ca9318018d18e3327578ce939fb99feaad6b2d7c8ab15f3de8db5"),
)


def git(root, *arguments):
    environment = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR"}}
    control = root.parent / "native-control"
    control.mkdir(exist_ok=True)
    (control / "hooks").mkdir(exist_ok=True)
    (control / "config").write_text("")
    environment.update(
        HOME=str(control),
        USERPROFILE=str(control),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_SYSTEM=str(control / "config"),
        GIT_CONFIG_GLOBAL=str(control / "config"),
        GIT_TERMINAL_PROMPT="0",
        GIT_OPTIONAL_LOCKS="0",
    )
    pins = (
        "core.hooksPath=" + str(control / "hooks"),
        "core.fsmonitor=false",
        "credential.helper=",
        "protocol.allow=never",
        "commit.gpgsign=false",
        "user.name=G1 Fixture",
        "user.email=fixture@example.invalid",
        "core.autocrlf=false",
        "gc.auto=0",
        "maintenance.auto=false",
    )
    result = subprocess.run(
        [
            str(GIT),
            "--no-pager",
            "--no-optional-locks",
            *[x for pin in pins for x in ("-c", pin)],
            *arguments,
        ],
        executable=str(GIT),
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=20,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.decode().strip()


@pytest.fixture
async def native(tmp_path, monkeypatch, request):
    mode = getattr(request, "param", "available")
    assert os.environ.get("SPROUT_G1_NATIVE_ENTRY") == "1", "Use the approved Host entry"
    source, worktree, exports, evidence = [
        tmp_path / n for n in ("source", "worktree", "exports", "evidence")
    ]
    for root in (source, worktree, exports, evidence):
        root.mkdir()
    spec = AcceptanceSpec.from_payload(
        {"cases": [dict(id="normal", target="src.app:summarize", args=[[]], expected_output={})]}
    )
    prepared = {
        "src/__init__.py": "",
        "src/app.py": "def summarize(x): return {}\n",
        "tests/frozen.py": render_acceptance_tests(spec),
        "examples/demo.py": render_acceptance_demo(spec),
    }
    extra = {
        "tracked.py": "staged_old=1\nunstaged_old=1\n",
        "deleted.py": "removed=1\n",
        "old_name.py": "rename_me=1\n",
        "private.py": "PRIVATE_NOT_SELECTED=1\n",
    }
    for root in (source, worktree):
        for path, text in {**prepared, **extra}.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text, encoding="utf-8", newline="")
        git(
            root,
            "init",
            "--initial-branch=fixture",
            "--template=" + str(root.parent / "native-control/hooks"),
            ".",
        )
        git(root, "add", "--", *prepared, *extra)
        git(root, "commit", "--no-verify", "-m", "new fixture baseline")
    baseline = git(worktree, "rev-parse", "HEAD")
    if mode != "empty":
        (worktree / "src/app.py").write_text("def summarize(x): return {'changed': 1}\n")
        (worktree / "tracked.py").write_text("staged_new=2\nunstaged_old=1\n")
        git(worktree, "add", "--", "tracked.py")
        (worktree / "tracked.py").write_text("staged_new=2\nunstaged_new=2\n")
        (worktree / "deleted.py").unlink()
        (worktree / "old_name.py").rename(worktree / "new_name.py")
        git(worktree, "add", "--", "old_name.py", "new_name.py")
        (worktree / "untracked.py").write_text("new_file=1\n")
    # These are Host-created inert fixture values, never executable scripts.
    # Production must not load source repo config/attributes or discover them.
    with (worktree / ".git/config").open("a") as stream:
        stream.write(
            "\n[diff]\n external = R04_MUST_NOT_EXECUTE\n"
            '[filter "blocked"]\n clean = R04_MUST_NOT_EXECUTE\n'
            " smudge = R04_MUST_NOT_EXECUTE\n"
        )
    (worktree / ".gitattributes").write_text("*.py filter=blocked diff=blocked\n")
    if mode == "hardlink":
        os.link(worktree / "tracked.py", worktree / "unselected-alias.py")
    paths = tuple(prepared) + (
        "tracked.py",
        "deleted.py",
        "old_name.py",
        "new_name.py",
        "untracked.py",
    )
    storage = StorageBundle.in_memory()
    storage.metadata = open_metadata_store(evidence / "native.db")
    runtime, _ = build_runtime(storage=storage)
    workspace = await runtime.open_workspace(str(source))
    principal = Principal(user_id="native-host-fixture", source="cli", authenticated=True)
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
    task = await runtime.create_task(
        workspace.id,
        "Review native fixture changes",
        actor=principal,
        source="cli",
        execution_mode="code",
        metadata={"session_id": session.id, "required_paths": list(prepared)},
        delegation_scope=DelegationScope(
            allowed_actions=frozenset({"tool.invoke", "file.read", "git.diff", "process.run"}),
            allowed_paths=paths,
        ),
    )
    task = await runtime.bind_task_acceptance(
        task.id,
        principal=principal,
        spec=spec.to_payload(),
        test_path="tests/frozen.py",
        demo_path="examples/demo.py",
    )
    docker = evidence / "docker-placeholder"
    docker.write_bytes(b"not executed")
    await runtime.bind_task_verification(
        task.id,
        principal,
        paths=tuple(prepared),
        export_parent=exports,
        images={"verification": "sha256:" + "a" * 64, "coding": "sha256:" + "b" * 64},
        source_digests={p: hashlib.sha256((source / p).read_bytes()).hexdigest() for p in prepared},
        discovery={
            "confirmed": True,
            "test_files": ["tests/frozen.py"],
            "config_files": [],
            "plugins": [],
            "plugin_autoload": False,
        },
        tools={
            "python": {"path": "/usr/bin/python", "sha256": "a" * 64},
            "pytest": {"path": "/usr/bin/pytest", "sha256": "b" * 64, "plugins": []},
            "ruff": {
                "path": "/usr/bin/ruff",
                "sha256": "c" * 64,
                "configuration_confirmed": True,
                "config_files": [],
            },
        },
        docker={
            "path": str(docker),
            "sha256": hashlib.sha256(docker.read_bytes()).hexdigest(),
            "context": "unit",
            "endpoint": "npipe://unit",
            "daemon_id": "unit",
        },
    )
    ref = SandboxRef(id=str(uuid4()), kind="git_worktree", root=worktree, worktree_ref="fixture")
    nodes = [
        ExecutionNode(
            id=str(uuid4()), task_id=task.id, type=t, budget=NodeBudget(timeout_seconds=180)
        )
        for t in (NodeType.SANDBOX, NodeType.AGENT, NodeType.EVALUATION, NodeType.APPROVAL)
    ]
    nodes = [
        replace(
            n,
            dependencies=() if i == 0 else (nodes[i - 1].id,),
            metadata={
                "test_commands": ["pytest"],
                "verification_commands": [{"command": "pytest -q", "required": True}],
            }
            if n.type is NodeType.EVALUATION
            else {},
        )
        for i, n in enumerate(nodes)
    ]
    flow = ExecutionGraph(task_id=task.id, nodes=tuple(nodes))
    broker = runtime._process_broker

    async def substitute(manifest, issuer, scope, exported, slot, **kwargs):
        plans = await broker.prepare_verification(manifest, issuer, scope, exported)
        plan = plans[slot]
        if plan["reason"] != "verification_native_commissioning_required":
            return dict(
                executed=False, passed=False, outcome=plan["outcome"], reason=plan["reason"]
            )
        args = broker._verification_obligation_arguments(manifest, issuer, slot)
        approval = await broker._approvals.request(
            "cli_tool_run",
            args,
            task_id=task.id,
            requested_by=principal.user_id,
            source="cli",
            session_id=session.id,
            resource_scope=workspace.id,
        )
        await broker._approvals.decide(approval.id, True, decided_by="controlled-test-human")
        assert await broker._approvals.is_approved(
            "cli_tool_run",
            args,
            task_id=task.id,
            requested_by=principal.user_id,
            source="cli",
            session_id=session.id,
            resource_scope=workspace.id,
        )
        current = await storage.metadata.get_task(task.id)
        node = await storage.metadata.get_execution_node(scope.node.id)
        saved = record(
            SimpleNamespace(rig=SimpleNamespace(task=current, node=node, workspace=workspace))
        )
        saved.update(
            id=str(uuid5(NAMESPACE_URL, task.id + ":ordinary:" + node.id + ":" + slot)),
            manifest=manifest.sha256,
            slot=slot,
        )
        assert await storage.metadata.claim_verification_execution(saved)
        is_pytest = slot in {"project-pytest", "strategy-pytest"} or slot.startswith("acceptance:")
        result = dict(
            executed=True,
            passed=not is_pytest,
            outcome="unverified" if is_pytest else "passed",
            exit_code=0,
            stdout="substituted verification, NOT actual Docker",
            stderr="",
            execution_id=saved["id"],
            retry_allowed=False,
        )
        assert await storage.metadata.finish_verification_execution(
            saved["id"], saved["owner"], "completed", result
        )
        return result

    monkeypatch.setattr(broker, "execute_verification_obligation", substitute)
    actual = runtime._executor(storage.metadata).handler(task, workspace, ReadPlan(task_id=task.id))

    async def handler(node):
        await runtime._persist_node_products()
        if node.type in {NodeType.SANDBOX, NodeType.AGENT}:
            current = await storage.metadata.get_task(task.id)
            with runtime._project_analysis_scope(current, workspace, node) as scope:
                if node.type is NodeType.SANDBOX:
                    await runtime._register_frozen_sandbox(ref)
                    await runtime.bind_task_git_view(
                        task.id,
                        principal,
                        executable=GIT,
                        executable_sha256="f" * 64 if mode == "unavailable" else GIT_SHA,
                        chain=CHAIN,
                        baseline=baseline,
                        metadata_root=worktree / ".git",
                        paths=paths,
                    )
                    output = dict(
                        id=ref.id, kind=ref.kind, root=str(ref.root), worktree_ref=ref.worktree_ref
                    )
                else:
                    runtime._node_publications[node.id] = scope
                    output = {"content": "Host prepared the native Git fixture; no model invoked"}
                runtime._remember_node_product(scope, output)
                return output
        if node.type is NodeType.EVALUATION and mode == "policy-deny":
            from Sprout.security.access import AccessDecision, ActionType, PolicyDecision

            original = runtime._git_broker._policy

            class Revoked:
                def decide(self, query):
                    if query.action is ActionType.GIT_DIFF:
                        return PolicyDecision(AccessDecision.DENY, "native test revoke Git read")
                    return original.decide(query)

            runtime._git_broker._policy = Revoked()
        if node.type is NodeType.APPROVAL and mode == "stale":
            (worktree / "src/app.py").write_text("# Host fixture changed after EVALUATION\n")
        return await actual(node)

    def integrity():
        return {
            "head": (worktree / ".git/HEAD").read_bytes().hex(),
            "head_oid": (worktree / ".git/refs/heads/fixture").read_text().strip(),
            "index": hashlib.sha256((worktree / ".git/index").read_bytes()).hexdigest(),
            "files": {
                p: hashlib.sha256((worktree / p).read_bytes()).hexdigest()
                if (worktree / p).exists()
                else None
                for p in (*paths, "private.py", ".gitattributes", ".git/config")
            },
        }

    before = integrity()
    try:
        yield SimpleNamespace(**locals())
    finally:
        storage.metadata.close()


async def test_native_git_broker_to_persistent_proposal(native):
    import asyncio

    from Sprout.tools.context_keys import _current_project_node

    n = native
    owner_proof = []
    original_review = n.runtime._git_broker.review_diff

    async def checked_review(workspace, *, scope):
        frame = _current_project_node()
        assert frame is not None and frame.owner is asyncio.current_task()
        binding = n.runtime._verification_hosts[n.task.id]["git"]
        # Real Runtime domain checks: copying ContextVars is not node ownership.
        await n.runtime._git_review_record(binding, n.runtime._git_broker, frame)
        with pytest.raises(PermissionError, match="execution domain unavailable"):
            await asyncio.create_task(n.runtime._git_review_record(
                binding, n.runtime._git_broker, frame))
        owner_proof.append({"task": n.task.id, "owner": frame.guard_owner,
                            "owner_allowed": True, "non_owner_denied": True})
        return await original_review(workspace, scope=scope)

    n.runtime._git_broker.review_diff = checked_review
    result = await SequentialOrchestrator(n.storage.metadata).execute(n.flow, handler=n.handler)
    proposals = await n.storage.metadata.list_change_proposals(n.task.id)
    assert len(proposals) == 1, (
        result,
        [
            (x.type.value, x.status.value, str(x.metadata.get("error"))[:150])
            for x in await n.storage.metadata.list_execution_nodes(n.task.id)
        ],
    )
    proposal = proposals[0]
    assert proposal.metadata["git_diff_status"] == "available", proposal.metadata.get("git_diff")
    evidence = proposal.metadata["git_diff"]
    assert evidence["task_id"] == n.task.id and evidence["baseline"] == n.baseline
    assert "staged_new" in evidence["patches"]["tracked"]
    assert "unstaged_new" in evidence["patches"]["tracked"]
    assert "staged_new" in evidence["patches"]["staged"]
    assert "unstaged_new" in evidence["patches"]["unstaged"]
    assert "new_file" in evidence["patches"]["untracked"]
    assert "deleted.py" in evidence["patches"]["tracked"]
    assert "rename from" in evidence["patches"]["tracked"]
    assert "private.py" not in json.dumps(evidence["patches"])
    private_data = b"PRIVATE_NOT_SELECTED=1\n"
    oid = hashlib.sha1(
        b"blob " + str(len(private_data)).encode() + b"\0" + private_data
    ).hexdigest()
    assert not any(
        Path(name).name == oid[2:] and Path(name).parent.name == oid[:2]
        for name in evidence["metadata"]
    )
    assert not any(Path(name).name == "config" for name in evidence["metadata"])
    assert proposal.diffs and proposal.metadata["content_diff"]["format"] == "unified_content_diff"
    assert proposal.metadata["verification"]["verification_report"]["verified"] is False
    assert n.before == n.integrity()
    from unittest.mock import AsyncMock

    from Sprout.runtime.changes import ChangeProposalService

    apply = AsyncMock(side_effect=AssertionError("No actual Apply"))
    service = ChangeProposalService(n.storage.metadata, n.storage.operational, apply)
    approved = await service.approve(proposal.id, decided_by="controlled-test-human")
    assert not (await service.apply(proposal.id)).applied
    apply.assert_not_called()
    report = {
        "owner_proof": owner_proof,
        "task_id": n.task.id,
        "result": str(result.status),
        "proposal": asdict(approved),
        "before": n.before,
        "after": n.integrity(),
        "database": str(n.evidence / "native.db"),
        "nodes": [
            {
                "id": x.id,
                "type": x.type.value,
                "status": x.status.value,
                "guard": x.metadata.get("execution_guard"),
            }
            for x in await n.storage.metadata.list_execution_nodes(n.task.id)
        ],
    }
    (n.evidence / "NATIVE_RESULT.json").write_text(
        json.dumps(report, default=str, indent=2), encoding="utf-8"
    )


@pytest.mark.parametrize(
    "native", ["unavailable", "policy-deny", "empty", "stale", "hardlink"], indirect=True
)
async def test_native_git_refusals_and_empty_view(native):
    n = native
    result = await SequentialOrchestrator(n.storage.metadata).execute(n.flow, handler=n.handler)
    proposals = await n.storage.metadata.list_change_proposals(n.task.id)
    if n.mode == "stale":
        assert not proposals
        nodes = await n.storage.metadata.list_execution_nodes(n.task.id)
        approval = next(x for x in nodes if x.type is NodeType.APPROVAL)
        assert "git_review_file_stale" in approval.metadata["error"]
        assert n.before["index"] == n.integrity()["index"]
        assert n.before["head_oid"] == n.integrity()["head_oid"]
        return
    assert len(proposals) == 1, result
    proposal = proposals[0]
    assert proposal.metadata["verification"]["verification_report"]["verified"] is False
    evidence = proposal.metadata["git_diff"]
    if n.mode == "empty":
        assert proposal.metadata["git_diff_status"] == "available"
        assert not any(evidence["patches"].values()) and not proposal.diffs
    else:
        assert proposal.metadata["git_diff_status"] == "git_diff_unavailable"
        assert not proposal.diffs
        expected = (
            "git_installation_changed"
            if n.mode == "unavailable"
            else "non_regular_or_shared_file"
            if n.mode == "hardlink"
            else "revoke"
        )
        assert expected in evidence["reason"]
    assert n.before == n.integrity()
    (n.evidence / "CASE_RESULT.json").write_text(
        json.dumps(
            {
                "mode": n.mode,
                "proposal": asdict(proposal),
                "before": n.before,
                "after": n.integrity(),
            },
            default=str,
            indent=2,
        )
    )
