"""Task entry, explicit process approvals and current controlled delivery.

Windows replacement delivery uses real Runtime/Guard/Receipt/G1/SQLite and
filesystem components with a controlled model. POSIX claims refusal only.
Retired delete/rename and old approval keys must never restore auto-commit.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from Sprout.cli.app import app
from Sprout.config.loader import default_settings
from Sprout.gateway.project_gateway import ProjectGateway
from Sprout.runtime.factory import create_runtime
from Sprout.tests.fake_model import FakeModel
from Sprout.tests.test_r04_e_apply_native import current_delivery as current_delivery

REQUIREMENT = "给 user 模块加一个导出 CSV 的函数"


async def _create_host_task(gateway, workspace, instruction):
    """Explicit first-party fixture resources; never derive grants from model edits."""
    from Sprout.cli.commands.project import _eval_scope_for_workspace
    from Sprout.gateway.local_identity import current_cli_principal

    principal = current_cli_principal()
    scope = _eval_scope_for_workspace(workspace, principal)
    paths = ("src/user.py", "src/export.py", "obsolete.py", "renamed.py")
    scope = replace(scope, allowed_paths=scope.allowed_paths + tuple(
        str(workspace.root / path) for path in paths))
    return await gateway.create_task(workspace.id, instruction, principal=principal,
                                     delegation_scope=scope,
                                     preparation_paths=("src/user.py", "pyproject.toml"))


def _repo(tmp_path: Path, *, gitignore: bool = True) -> Path:
    """A minimal git workspace with one test that passes.

    ``gitignore=False`` models the repository an operator actually points Sprout
    at: one that has not enumerated its caches. The proposal path must not
    depend on the repository having done that bookkeeping for it.
    """
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "user.py").write_text(
        '"""User service."""\n\n\ndef list_users():\n    return [{"id": 1}]\n',
        encoding="utf-8",
    )
    (root / "tests" / "test_user.py").write_text(
        "from src.user import list_users\n\n\ndef test_list_users():\n"
        "    assert list_users()\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    # Verification runs pytest inside the sandbox, which writes __pycache__.
    # A real repository ignores those; without this the fixture would model a
    # project whose bytecode caches look like agent edits.
    #
    # The sandbox filters them out on its own (``_SANDBOX_EXCLUDES``), so this
    # is no longer load-bearing for correctness — but a repository that ignores
    # its caches is still the common shape, and the suite should exercise it.
    if gitignore:
        (root / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    # pytest needs the package importable from the sandbox worktree.
    (root / "conftest.py").write_text(
        "import pathlib, sys\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n",
        encoding="utf-8",
    )
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "tester"],
        ["add", "-A"],
        ["commit", "-qm", "init"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def _runtime(tmp_path: Path, model: FakeModel):
    """A runtime whose agent runs on the scripted model.

    The factory captures ``models.default()`` when it builds the agent, so
    registering a model afterwards does not reach it — the agent keeps the
    provider it was assembled with. The agent is therefore rebuilt here with
    the scripted model, reusing the executor the factory already wired.
    """
    from Sprout.agent.loop import AgentLoop
    from Sprout.agent.planner import DirectPlanner
    from Sprout.agent.router import AgentRouter

    settings = default_settings()
    settings.model.provider = "echo"
    settings.model.model = "echo-1"
    db = (tmp_path / "meta.db").as_posix()
    settings.storage.operational = f"sqlite:///{(tmp_path / 'op.db').as_posix()}"
    settings.storage.knowledge = "memory://"
    settings.storage.observations.enabled = False
    settings.storage.metadata = f"sqlite:///{db}"
    settings.storage.session = f"sqlite:///{db}"
    settings.storage.trajectory_dir = str(tmp_path / "traj")
    settings.storage.blobs_dir = str(tmp_path / "blobs")
    settings.storage.project_root = str(tmp_path / "projects")
    settings.security.audit.path = str(tmp_path / "audit.jsonl")
    runtime = create_runtime(settings)

    runtime.models.register(model, default=True)
    existing = runtime.agents.get(runtime.default_agent)
    scripted = AgentLoop(
        model=model,
        executor=existing.executor,
        max_steps=existing.max_steps,
        planner=DirectPlanner(),
    )
    runtime.register_agent(runtime.default_agent, scripted, default=True)
    runtime.set_router(AgentRouter(runtime.agents, default=runtime.default_agent))
    return runtime


def _commit_count(repo: Path) -> int:
    out = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(out.stdout.strip())


@pytest.mark.parametrize("decision", ["approve", "reject", "cancel"])
@pytest.mark.parametrize("mutation", ["delete", "rename"])
def test_delete_approval_survives_restart_and_controls_commit(tmp_path, decision, mutation):
    """Legacy deletion approvals persist, but cannot authorize R04 Apply.

    This is rejection coverage for a retired delivery operation, not a claim
    that deletion/rename delivery succeeds. Supported replacement delivery is
    exercised by test_r04_e_apply_native with a real Runtime Receipt and Plan.
    """
    from Sprout.execution.models import ChangeProposal, DiffResult

    repo = _repo(tmp_path)
    obsolete = repo / "obsolete.py"
    obsolete.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "obsolete.py"], cwd=repo,
                   check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "add obsolete"], cwd=repo,
                   check=True, capture_output=True)
    before = _commit_count(repo)
    original = obsolete.read_bytes()
    patch = ("--- a/obsolete.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-value = 1\n"
             if mutation == "delete" else
             "diff --git a/obsolete.py b/renamed.py\nsimilarity index 100%\n"
             "rename from obsolete.py\nrename to renamed.py\n")

    async def run():
        runtime = _runtime(tmp_path, FakeModel())
        workspace = await runtime.open_workspace(repo)
        gateway = ProjectGateway(runtime, transport="cli", default_user="display")
        task = await _create_host_task(gateway, workspace, REQUIREMENT)
        # Deliberately untrusted legacy proposal: even a real persisted human
        # decision on this old key cannot replace approval of a live Plan.
        proposal = ChangeProposal(task_id=task.id,
            files_changed=(("obsolete.py",) if mutation == "delete" else
                           ("obsolete.py", "renamed.py")),
            diffs=(DiffResult(path="obsolete.py", diff_text=patch),))
        await runtime.storage.metadata.save_change_proposal(proposal)
        approval = await runtime._approvals.request("file_delete",
            {"path": "obsolete.py", "mutation": mutation}, task_id=task.id,
            session_id=task.metadata["session_id"], requested_by=task.actor.user_id,
            source=task.source, resource_scope=workspace.id)
        await runtime.stop()
        restarted = _runtime(tmp_path, FakeModel())
        try:
            restored = await restarted.get_task(task.id)
            assert restored.actor == task.actor
            record = await restarted._approvals.store.get_approval(approval.id)
            assert record.status.value == "pending"
            if decision == "cancel":
                cancelled = await restarted.cancel_task(task.id)
                assert cancelled.status.value == "cancelled"
            else:
                await restarted._approvals.decide(approval.id, decision == "approve",
                    decided_by="offline-fixture-human", channel="cli")
            persisted = await restarted._approvals.store.get_approval(approval.id)
            expected = {"approve": "approved", "reject": "rejected"}
            if decision != "cancel":
                assert persisted.status.value == expected[decision]
            assert persisted.used_at is None
            result = await restarted._apply_broker.apply(proposal, workspace,
                scope=restored.delegation_scope, principal=restored.actor)
            assert not result.applied
            assert result.reason == "internal_apply_execution_unavailable"
            stored = await restarted.storage.metadata.get_change_proposal(proposal.id)
            assert stored.status.value == "pending"
            assert not restarted._local_apply_plans
            assert not restarted._local_acceptance_receipts
            assert (await restarted._approvals.store.get_approval(approval.id)).used_at is None
        finally:
            await restarted.stop()

    asyncio.run(run())
    assert obsolete.read_bytes() == original
    assert not (repo / "renamed.py").exists()
    assert _commit_count(repo) == before
    assert not subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                              check=True, capture_output=True).stdout




def test_requirement_reaches_the_agent_as_a_planned_task(tmp_path: Path) -> None:
    """The bridge is wired: a real entry point produces a planned task.

    Regression: ``create_task_from_strategy`` had no callers, so
    ``task.metadata["steps"]`` was always empty and the whole decomposition
    path ran only under tests.
    """
    repo = _repo(tmp_path)
    model = FakeModel(plan_steps=["读 user 模块"])

    async def run() -> dict:
        runtime = _runtime(tmp_path, model)
        workspace = await runtime.open_workspace(repo)
        gateway = ProjectGateway(runtime, transport="cli", default_user="t")
        task = await _create_host_task(gateway, workspace, REQUIREMENT)
        await runtime.stop()
        return dict(task.metadata)

    metadata = __import__("asyncio").run(run())

    assert metadata["plan_kind"] in {"local", "model"}
    assert "required_paths" in metadata


@pytest.mark.parametrize("correct", [True, False])
def test_requirement_output_controls_whether_the_change_lands(current_delivery, correct):
    """Real Windows replacement delivery; POSIX is explicitly refusal-only."""
    if current_delivery["platform"] == "posix":
        assert current_delivery["positive_apply"] == "NOT_SUPPORTED"
        return
    evidence = current_delivery[correct]
    assert evidence["bytes"] == evidence["expected"]
    if correct:
        assert evidence["verification"]["verified"]
        assert evidence["transaction"]["phase"] == "COMPLETED"
        grants = [a for a in evidence["approval"] if a["tool"] == "file.apply"]
        assert len(grants) == 1 and grants[0]["status"] == "consumed"
    else:
        assert not evidence["verification"]["verified"]
        assert not evidence["proposals"] and evidence["transaction"] is None


def test_a_failing_verification_does_not_land(current_delivery):
    """A real failed frozen check cannot mint a Receipt or executable Plan."""
    if current_delivery["platform"] == "posix":
        assert current_delivery["positive_apply"] == "NOT_SUPPORTED"
        return
    evidence = current_delivery[False]
    evaluations = [n for n in evidence["nodes"] if n["type"] == "evaluation"]
    assert evaluations and evaluations[0]["metadata"]["output"]["status"] == "failed"
    results = evaluations[0]["metadata"]["output"]["test_results"]
    assert any(r["executed"] and not r["passed"] for r in results)
    assert not evidence["verification"]["verified"]
    assert not evidence["proposals"] and evidence["transaction"] is None
    assert evidence["bytes"] == evidence["expected"]


@pytest.mark.asyncio
async def test_a_withheld_command_parks_the_task(tmp_path: Path) -> None:
    """A withheld ordinary process creates a real pending record, not a red test.

    EVALUATION's same-coroutine approval wait is covered separately by the
    Windows native approval-wait suite; this checks the public Task process API.
    """
    runtime, task, command = await _ordinary_process_task(tmp_path)
    try:
        before = _commit_count(tmp_path / "repo")
        result = await runtime.run_task_process(task.id, command)
        assert result.needs_approval and not result.allowed and not result.started
        record = await runtime._approvals.store.get_approval(result.approval_id)
        assert record.status.value == "pending"
        assert record.task_id == task.id
        # This legacy ordinary-process API is Task-scoped, not the frozen
        # EVALUATION Session permit. It must not invent a Session grant.
        assert record.session_id == ""
        await runtime._sessions.require_owner(task.metadata["session_id"], task.actor)
        assert record.requested_by == task.actor.user_id and record.used_at is None
        assert not (tmp_path / "repo" / "process-ran.txt").exists()
        report = await runtime.get_verification_report(task.id)
        assert not report["verified"] and not report["checks"]
        assert not await runtime.list_change_proposals(task.id)
        assert _commit_count(tmp_path / "repo") == before
    finally:
        await runtime.stop()


def test_a_proposal_offers_only_the_agents_edits(current_delivery):
    """G1 and the executable Plan name only the actual replacement, no caches."""
    if current_delivery["platform"] == "posix":
        assert current_delivery["positive_apply"] == "NOT_SUPPORTED"
        return
    evidence = current_delivery[True]
    proposal, = evidence["proposals"]
    assert proposal["files_changed"] == ("src/app.py",)
    assert proposal["metadata"]["git_diff"]["status"] == "available"
    assert len(proposal["metadata"]["git_diff"]["commands"]) == 5
    assert all("__pycache__" not in d["diff_text"] and ".pyc" not in d["diff_text"]
               for d in proposal["diffs"])
    assert evidence["transaction"]["phase"] == "COMPLETED"


def _cli_config(tmp_path: Path) -> Path:
    """A minimal ``sprout.toml`` for the approval CLI.

    Only ``load_settings`` has to succeed — the runtime under test is injected —
    so these DSNs are never opened. They point inside ``tmp_path`` regardless,
    so a mistake cannot reach the operator's real ``~/.sprout`` databases.
    """
    config = tmp_path / "sprout.toml"
    config.write_text(
        '[model]\nprovider = "echo"\nmodel = "echo-1"\n'
        "\n[storage]\n"
        f'operational = "sqlite:///{(tmp_path / "op.db").as_posix()}"\n'
        f'knowledge = "sqlite:///{(tmp_path / "knowledge.db").as_posix()}"\n'
        f'metadata = "sqlite:///{(tmp_path / "meta.db").as_posix()}"\n'
        'session = "memory://"\n'
        "\n[storage.observations]\n"
        "enabled = false\n",
        encoding="utf-8",
    )
    return config


def test_approving_a_withheld_command_wakes_the_parked_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real CLI decision permits the held ordinary Task process to execute.

    Do not resume an exited EVALUATION or reconstruct its private authority.
    The live Runtime owns the request, SQLite decision and actual subprocess.
    """
    runtime, task, command = asyncio.run(_ordinary_process_task(tmp_path))
    try:
        pending = asyncio.run(runtime.run_task_process(task.id, command))
        assert pending.needs_approval and not pending.started
        assert not (tmp_path / "repo" / "process-ran.txt").exists()
        monkeypatch.setattr("Sprout.runtime.factory.create_runtime", lambda *_a, **_kw: runtime)
        result = CliRunner().invoke(app, ["approvals", "approve", pending.approval_id,
            "--config", str(_cli_config(tmp_path))])
        assert result.exit_code == 0, result.output
        assert "approved" in result.output

        async def inspect():
            record = await runtime._approvals.store.get_approval(pending.approval_id)
            assert record.status.value == "approved"
            outcome = await runtime.run_task_process(task.id, command)
            assert outcome.allowed and outcome.started and outcome.exit_code == 0
            assert "held process executed" in outcome.stdout
            assert int(outcome.stdout.split()[-1]) > 0
            assert (tmp_path / "repo" / "process-ran.txt").read_text() == "once"
            assert not await runtime.list_change_proposals(task.id)
            assert not await runtime.storage.metadata.list_execution_nodes(task.id)
        asyncio.run(inspect())
    finally:
        asyncio.run(runtime.stop())


async def _ordinary_process_task(tmp_path):
    import sys

    from Sprout.task.models import DelegationScope

    root = _repo(tmp_path)
    runtime = _runtime(tmp_path, FakeModel())
    workspace = await runtime.open_workspace(root)
    task = await _create_host_task(ProjectGateway(runtime, transport="cli", default_user="display"),
                                   workspace, REQUIREMENT)
    # Host-selected process cwd only; this fixture grants no file read/write.
    task = replace(task, delegation_scope=DelegationScope(allowed_paths=(str(root),),
        allowed_actions=frozenset({"process.run"})))
    await runtime.storage.metadata.save_task(task)
    command = (sys.executable, "-I", "-c",
        "import os; from pathlib import Path; p=Path('process-ran.txt'); "
        "assert not p.exists(); p.write_text('once'); print('held process executed', os.getpid())")
    return runtime, task, command
