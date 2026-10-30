"""Native Windows Local Receipt → whole-plan approval → conditional Workspace Apply.

Only the LLM is scripted. Broker, Guard, Receipt, SQLite and file writes are real.
Fault injection wraps actual writes; it never supplies successful write results.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from Sprout.execution.acceptance import (
    render_acceptance_demo,
    render_acceptance_tests,
)
from Sprout.gateway.local_identity import current_cli_principal
from Sprout.gateway.rpc_gateway import RPCGateway
from Sprout.llm.messages import LLMResponse, ToolCall
from Sprout.orchestration.models import ExecutionGraph, ExecutionNode, NodeBudget, NodeType
from Sprout.orchestration.service import SequentialOrchestrator
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.security.engine import PolicyEngine
from Sprout.session.models import Session
from Sprout.storage.bundle import StorageBundle
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.storage.local.sqlite.operational import open_operational_store
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import DelegationScope, Task
from Sprout.tests.conftest import ScriptedModel, build_runtime
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
        "single",
        "multi",
        "conflict",
        "no_approval",
        "partial",
        "unknown",
        "changed_plan",
        "wrong_owner",
        "no_identity",
        "owner_coroutine",
        "source_replace",
        "review_only",
        "partial_input",
        "unverified_input",
        "changed_approval",
        "plan_after_approval",
    ],
)
async def test_native_apply_vertical(tmp_path, monkeypatch, mode):
    context = await prepare_apply_fixture(tmp_path, monkeypatch, mode)
    runtime, storage, task, root, principal, files, targets, old, new = (
        context[k]
        for k in (
            "runtime",
            "storage",
            "task",
            "root",
            "principal",
            "files",
            "targets",
            "old",
            "new",
        )
    )
    row = None
    try:
        result = await runtime._execute_task(task, artifact_snapshot=None)
        nodes = await storage.metadata.list_execution_nodes(task.id)
        (tmp_path / "PRE_APPROVAL.json").write_text(
            json.dumps(
                {
                    "result": str(result),
                    "nodes": [
                        {"type": n.type.value, "status": n.status.value, "metadata": n.metadata}
                        for n in nodes
                    ],
                },
                default=str,
                indent=2,
            )
        )
        proposals = await storage.metadata.list_change_proposals(task.id)
        assert proposals, (result, [(n.type, n.status, n.metadata.get("error")) for n in nodes])
        proposal = proposals[0]
        from dataclasses import fields

        original = runtime._local_apply_plans[proposal.id]["proposal"]
        (tmp_path / "ROUNDTRIP.json").write_text(
            json.dumps(
                {
                    f.name: {
                        "actual": repr(getattr(proposal, f.name)),
                        "expected": repr(getattr(original, f.name)),
                    }
                    for f in fields(original)
                    if getattr(proposal, f.name) != getattr(original, f.name)
                },
                indent=2,
            )
        )
        assert runtime._local_acceptance_receipts
        receipt, (payload, _) = next(iter(runtime._local_acceptance_receipts.items()))
        assert receipt.issuer is runtime and payload["guard_state"] == "completed"
        assert proposal.metadata["apply_plan"]["receipt_sha256"] == receipt.payload_sha256
        assert proposal.status.value == "pending"
        git_evidence = proposal.metadata["git_diff"]
        assert git_evidence["status"] == "available"
        assert git_evidence["source"] == "git_no_index_managed_view"
        assert git_evidence["coverage"]["status"] == "partial"
        assert len(git_evidence["commands"]) == 5
        assert all(c["exit_code"] in {0, 1} for c in git_evidence["commands"])
        assert proposal.metadata["diff_format"] == "git_no_index_managed_view"
        assert task.id not in runtime._verification_hosts
        from Sprout.tests.r04_g1_native_cases import git as fixture_git

        binding = runtime._local_git_reviews[task.id]["git"]
        actual_git = fixture_git(
            binding.sandbox.root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "HEAD",
            "--",
            *binding.paths,
        )
        managed_git = (
            git_evidence["patches"]["tracked"].replace("a/base/", "a/").replace("b/current/", "b/")
        )
        assert actual_git == managed_git.strip()
        shown = await RPCGateway(runtime).handle(
            {"id": 0, "method": "proposal.show", "params": {"proposal_id": proposal.id}},
            principal=principal,
        )
        assert shown["result"]["git_diff"]["sha256"] == git_evidence["sha256"]
        for p in targets:
            assert (root / p).read_bytes() == old.encode()
        transaction = str(uuid5(NAMESPACE_URL, "ordinary-apply:" + proposal.id))
        rpc = RPCGateway(runtime)
        if mode == "no_approval":
            response = await rpc.handle(
                {"id": 1, "method": "proposal.apply", "params": {"proposal_id": proposal.id}},
                principal=principal,
            )
            assert not (response.get("result") or {}).get("applied")
        elif mode == "changed_plan":
            changed = dict(proposal.metadata)
            changed["apply_plan"] = {**changed["apply_plan"], "patch": "replaced"}
            await storage.metadata.save_change_proposal(replace(proposal, metadata=changed))
            response = await rpc.handle(
                {"id": 1, "method": "proposal.approve", "params": {"proposal_id": proposal.id}},
                principal=principal,
            )
            assert "error" in response
        elif mode in {"review_only", "partial_input", "unverified_input"}:
            changed = dict(proposal.metadata)
            if mode == "review_only":
                changed["review_only"] = True
            elif mode == "partial_input":
                changed["change_coverage"] = {"status": "partial"}
            else:
                changed["verification"] = {"verified": False}
            await storage.metadata.save_change_proposal(replace(proposal, metadata=changed))
            response = await rpc.handle(
                {"id": 1, "method": "proposal.approve", "params": {"proposal_id": proposal.id}},
                principal=principal,
            )
            assert "error" in response
        elif mode == "no_identity":
            response = await rpc.handle(
                {"id": 1, "method": "proposal.approve", "params": {"proposal_id": proposal.id}}
            )
            assert "error" in response
        elif mode == "wrong_owner":
            response = await rpc.handle(
                {"id": 1, "method": "proposal.approve", "params": {"proposal_id": proposal.id}},
                principal=replace(principal, user_id="not-owner"),
            )
            assert "error" in response
        else:
            if mode == "conflict":
                (root / targets[0]).write_bytes(b"host change\n")
            if mode == "source_replace":
                candidate = next(iter(runtime._local_acceptance_receipts.values()))[1]
                (candidate["sandbox"].root / targets[0]).write_bytes(b"changed after receipt\n")
            if mode == "owner_coroutine":
                original_apply = runtime._apply_broker.apply

                async def child_owner(*args, **kwargs):
                    return await asyncio.create_task(original_apply(*args, **kwargs))

                monkeypatch.setattr(runtime._apply_broker, "apply", child_owner)
            if mode == "plan_after_approval":
                original_decide = runtime._approvals.decide

                async def changed_plan_decide(approval_id, *args, **kwargs):
                    result = await original_decide(approval_id, *args, **kwargs)
                    altered = {
                        **proposal.metadata,
                        "apply_plan": {**proposal.metadata["apply_plan"], "patch": "replaced"},
                    }
                    await storage.metadata.save_change_proposal(replace(proposal, metadata=altered))
                    return result

                monkeypatch.setattr(runtime._approvals, "decide", changed_plan_decide)
            if mode == "changed_approval":
                original_decide = runtime._approvals.decide

                async def changed_decide(approval_id, *args, **kwargs):
                    result = await original_decide(approval_id, *args, **kwargs)
                    grant = await runtime._approvals.store.get_approval(approval_id)
                    # save_approval intentionally cannot rewrite fingerprints.
                    # Inject corruption only into this isolated test database.
                    await runtime._approvals.store._db.execute(
                        "UPDATE approvals SET arguments_fingerprint=? WHERE id=?",
                        ("0" * 64, grant.id),
                    )
                    return result

                monkeypatch.setattr(runtime._approvals, "decide", changed_decide)
            original_write = runtime._file_broker._conditional_apply_write
            calls = []

            async def writing(permit):
                value = await original_write(permit)
                calls.append(value)
                if mode == "unknown":
                    raise OSError("injected lost observation after actual write")
                if mode == "partial" and len(calls) == 1:
                    (root / targets[1]).write_bytes(b"host concurrent change\n")
                return value

            monkeypatch.setattr(runtime._file_broker, "_conditional_apply_write", writing)
            response = await rpc.handle(
                {"id": 1, "method": "proposal.approve", "params": {"proposal_id": proposal.id}},
                principal=principal,
            )
            if mode == "plan_after_approval":
                assert "error" in response
            else:
                assert "error" not in response, response
        row = await storage.metadata.get_apply_transaction(transaction)
        final = await storage.metadata.get_change_proposal(proposal.id)
        nodes = await storage.metadata.list_execution_nodes(task.id)
        approvals = await runtime._approvals.store.list_approvals()
        from dataclasses import asdict

        evidence = {
            "approvals": [asdict(a) for a in approvals],
            "mode": mode,
            "task": asdict(await storage.metadata.get_task(task.id)),
            "response": response,
            "receipt": payload,
            "plan": proposal.metadata["apply_plan"],
            "transaction": row,
            "proposal_status": final.status.value,
            "nodes": [
                {"type": n.type.value, "status": n.status.value, "metadata": n.metadata}
                for n in nodes
            ],
            "bytes": {p: (root / p).read_bytes().hex() for p in targets},
        }
        (tmp_path / "RESULT.json").write_text(json.dumps(evidence, default=str, indent=2))
        if mode in {"single", "multi"}:
            assert row and row["phase"] == "COMPLETED", evidence
            assert final.status.value == "applied"
            exact = [a for a in approvals if a.tool == "file.apply"]
            assert len(exact) == 1 and exact[0].status.value == "consumed"
            assert exact[0].arguments_fingerprint == runtime._approvals.fingerprint(
                __import__("Sprout.execution.apply_plan", fromlist=["arguments"]).arguments(
                    runtime._local_apply_plans[proposal.id]
                )
            )
            again = await rpc.handle(
                {"id": 3, "method": "proposal.approve", "params": {"proposal_id": proposal.id}},
                principal=principal,
            )
            assert "error" not in again
            assert (
                len(
                    [
                        a
                        for a in await runtime._approvals.store.list_approvals()
                        if a.tool == "file.apply"
                    ]
                )
                == 1
            )
            before_repeat = {p: (root / p).read_bytes() for p in targets}
            repeat = await rpc.handle(
                {"id": 2, "method": "proposal.apply", "params": {"proposal_id": proposal.id}},
                principal=principal,
            )
            assert not repeat.get("result", {}).get("applied")
            assert before_repeat == {p: (root / p).read_bytes() for p in targets}
            assert all(
                (root / f["path"]).read_bytes() == f["new_text"].encode()
                and f["new_text"].replace("\r\n", "\n") == new
                and hashlib.sha256((root / f["path"]).read_bytes()).hexdigest() == f["after"]
                for f in proposal.metadata["apply_plan"]["files"]
            )
        elif mode in {"partial", "unknown"}:
            assert row and row["phase"] == mode.upper(), evidence
            assert final.status.value == "approved"
            contents = {
                f["path"]: f["new_text"].encode() for f in proposal.metadata["apply_plan"]["files"]
            }
            assert (root / targets[0]).read_bytes() == contents[targets[0]]
            if mode == "partial":
                assert (root / targets[1]).read_bytes() == b"host concurrent change\n"
                assert [row["files"][p]["status"] for p in targets] == ["WRITTEN", "REJECTED"]
            else:
                assert row["files"][targets[0]]["status"] == "UNKNOWN"
                assert row["files"][targets[0]]["after"] is None
            before = {p: (root / p).read_bytes() for p in targets}
            repeat = await rpc.handle(
                {"id": 2, "method": "proposal.apply", "params": {"proposal_id": proposal.id}},
                principal=principal,
            )
            assert not (repeat.get("result") or {}).get("applied")
            assert before == {p: (root / p).read_bytes() for p in targets}
        else:
            assert row is None, evidence
            if mode == "plan_after_approval":
                assert final.metadata["apply_plan"]["patch"] == "replaced"
                assert any(
                    a.tool == "file.apply" and a.status.value == "approved" for a in approvals
                )
            if mode != "conflict":
                assert all((root / p).read_bytes() == old.encode() for p in targets)
        assert all(
            (root / p).read_bytes() == text.encode()
            for p, text in files.items()
            if p not in targets
        )
    finally:
        await runtime.stop()
        assert not runtime._local_apply_plans and not runtime._local_apply_permits
        assert not runtime._local_acceptance_receipts
        assert not runtime._local_git_reviews
        storage.metadata.close()


async def prepare_apply_fixture(tmp_path, monkeypatch, mode):
    assert os.name == "nt"
    git = Path("D:/dev/Git/mingw64/bin/git.exe")
    assert hashlib.sha256(git.read_bytes()).hexdigest() == (
        "cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96"
    )
    monkeypatch.setenv("PATH", str(git.parent) + os.pathsep + os.environ.get("PATH", ""))
    root = tmp_path / "repo"
    root.mkdir()
    spec = AcceptanceSpec.from_payload(
        {"cases": [{"id": "normal", "target": "src.app:echo", "args": [2], "expected_output": 2}]}
    )
    old = "def echo(value):\n    return value + 0\n"
    new = "def echo(value):\n    return value\n"
    files = {
        "pytest.ini": "[pytest]\naddopts = -p no:cacheprovider\n",
        "src/__init__.py": "",
        "src/app.py": old,
        "tests/frozen.py": render_acceptance_tests(spec),
        "examples/demo.py": render_acceptance_demo(spec),
    }
    targets = ["src/app.py"]
    if mode in {"multi", "partial"}:
        files["src/other.py"] = old
        targets.append("src/other.py")
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
        "user.name=Apply fixture",
        "-c",
        "user.email=apply@example.invalid",
        "-C",
        str(root),
    ]
    for args in [("init",), ("add", "--", *files), ("commit", "-m", "fixture")]:
        subprocess.run([*base, *args], env=env, capture_output=True, check=True, timeout=15)
    storage = StorageBundle.in_memory()
    storage.metadata = open_metadata_store(tmp_path / "apply.db")
    storage.operational = open_operational_store(str(tmp_path / "audit.db"))
    model = ScriptedModel(
        [
            LLMResponse(content="Make the exact replacement."),
            LLMResponse(
                tool_calls=tuple(
                    ToolCall(str(uuid4()), "sandbox_write_file", {"path": p, "content": new})
                    for p in targets
                )
            ),
            LLMResponse(content="Edited."),
        ]
    )
    runtime, _ = build_runtime(storage=storage, model=model)
    runtime._orchestrator_backend = SequentialOrchestrator(storage.metadata)
    runtime._trajectory_dir = tmp_path / "trajectories"
    policy = FixturePolicy()
    runtime._policy_engine = policy
    runtime._file_broker._policy = policy
    runtime._process_broker._policy = policy
    workspace = Workspace(id=str(uuid4()), root=root, kind=WorkspaceKind.GIT_REPOSITORY)
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
        instruction="Simplify echo",
        metadata={
            "execution_mode": "code",
            "session_id": session.id,
            "required_paths": list(files),
        },
        delegation_scope=DelegationScope(
            allowed_paths=(str(tmp_path),),
            allowed_actions=frozenset(
                {"tool.invoke", "file.read", "file.write", "file.apply", "git.diff", "process.run"}
            ),
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
    read = ExecutionNode(task_id=task.id, type=NodeType.READ)
    sandbox = ExecutionNode(task_id=task.id, type=NodeType.SANDBOX, dependencies=(read.id,))
    plan = ExecutionNode(task_id=task.id, type=NodeType.PLAN, dependencies=(read.id, sandbox.id))
    design = ExecutionNode(task_id=task.id, type=NodeType.TEST_DESIGN, dependencies=(sandbox.id,))
    agent = ExecutionNode(
        task_id=task.id,
        type=NodeType.AGENT,
        dependencies=(plan.id, sandbox.id),
        metadata={"tool_names": ["sandbox_write_file"]},
    )
    evaluation = ExecutionNode(
        task_id=task.id,
        type=NodeType.EVALUATION,
        dependencies=(agent.id, design.id),
        budget=NodeBudget(timeout_seconds=180),
    )
    approval = ExecutionNode(
        task_id=task.id,
        type=NodeType.APPROVAL,
        dependencies=(evaluation.id,),
        budget=NodeBudget(timeout_seconds=180),
    )
    apply = ExecutionNode(task_id=task.id, type=NodeType.APPLY, dependencies=(approval.id,))
    graph = ExecutionGraph(
        task_id=task.id, nodes=(read, sandbox, plan, design, agent, evaluation, approval, apply)
    )
    monkeypatch.setattr(runtime._graph_builder, "build", lambda *a, **kw: graph)
    if mode != "cli":
        exports = tempfile.mkdtemp(prefix="srg1-native-")
        await runtime.configure_local_git_review(
            task.id,
            principal,
            executable=git,
            executable_sha256="cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96",
            export_parent=exports,
        )
    return locals()


@pytest.mark.parametrize("approved", [True, False])
def test_native_cli_review_apply(tmp_path, monkeypatch, approved):
    from typer.testing import CliRunner

    from Sprout.cli.app import app
    from Sprout.cli.commands import project
    from Sprout.gateway.project_gateway import ProjectGateway

    context = asyncio.run(prepare_apply_fixture(tmp_path, monkeypatch, "cli"))
    runtime, task = context["runtime"], context["task"]
    gateway = ProjectGateway(runtime, transport="cli", default_user="unused-not-an-identity")
    monkeypatch.setattr(project, "_gateway", lambda config: gateway)
    result = CliRunner().invoke(
        app,
        [
            "project",
            "task-run",
            task.id,
            "--review-apply",
            "--git-executable",
            "D:/dev/Git/mingw64/bin/git.exe",
            "--git-sha256",
            "cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96",
        ],
        input="y\n" if approved else "n\n",
    )
    (tmp_path / "CLI.log").write_text(result.output)
    assert result.exit_code == 0, (result.output, result.exception)
    assert (
        "proposal_status=applied" if approved else "Not approved; no Apply write"
    ) in result.output
    assert result.output.count("Approve this entire exact Apply Plan?") == 1
    assert not runtime._local_apply_plans and not runtime._local_acceptance_receipts
    # Reopen only the test database: the CLI correctly closed this Runtime.
    store = open_metadata_store(tmp_path / "apply.db")

    async def query():
        proposals = await store.list_change_proposals(task.id)
        assert len(proposals) == 1
        assert proposals[0].status.value == ("applied" if approved else "pending")
        plan = proposals[0].metadata["apply_plan"]
        row = await store.get_apply_transaction(
            str(uuid5(NAMESPACE_URL, "ordinary-apply:" + proposals[0].id))
        )
        if approved:
            assert row["phase"] == "COMPLETED"
            assert (context["root"] / "src/app.py").read_bytes() == plan["files"][0][
                "new_text"
            ].encode()
        else:
            assert row is None
            assert (context["root"] / "src/app.py").read_bytes() == context["old"].encode()
        (tmp_path / "CLI_RESULT.json").write_text(
            json.dumps(
                {
                    "task_id": task.id,
                    "proposal_id": proposals[0].id,
                    "plan": plan,
                    "transaction": row,
                },
                default=str,
                indent=2,
            )
        )

    try:
        asyncio.run(query())
    finally:
        store.close()
