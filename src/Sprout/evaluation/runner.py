"""Run fixed first-party fixtures through public Runtime APIs, offline."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path

from Sprout.evaluation.fixtures import ANALYSIS_FILES, CASES, EvalCase
from Sprout.evaluation.model import ScriptedModel
from Sprout.execution.git_env import git_args, git_env

# The oracle lives outside the model's workspace and ignores pytest/conftest.
# It is independent expected-output evidence, not an OS security sandbox.
ORACLE = """import importlib, json, sys
sys.path.insert(0, sys.argv[1])
spec = json.loads(sys.argv[2])
results = []
for case in spec['cases']:
    try:
        module, name = case['target'].split(':')
        actual = getattr(importlib.import_module(module), name)(*case['args'], **case['kwargs'])
        results.append({'id': case['id'], 'passed': actual == case['expected_output'],
                        'actual': actual})
    except Exception as exc:
        results.append({'id': case['id'], 'passed': False, 'error': type(exc).__name__})
print(json.dumps(results))
sys.exit(0 if all(item['passed'] for item in results) else 1)
"""


def _write(path: Path, content: str) -> None:
    """Write generated fixture/evidence files only under the new run directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _json(path: Path, value) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _git(repo: Path, *args: str) -> str:
    environment = git_env()
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    result = subprocess.run(
        [
            "git",
            *git_args(
                "-c",
                "core.hooksPath=" + str(repo / ".disabled-hooks"),
                "-c",
                "commit.gpgsign=false",
                "-C",
                str(repo),
                *args,
            ),
        ],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _repo(directory: Path, case: EvalCase) -> Path:
    repo = directory / "repo"
    files = {
        "pyproject.toml": "[project]\nname='sprout-eval-fixture'\nversion='0.1.0'\n"
        "[tool.ruff]\nline-length=100\n"
        "[tool.ruff.lint]\nselect=['E','F','I','UP','B']\n",
        "pytest.ini": "[pytest]\naddopts=-p no:cacheprovider\n",
        "src/__init__.py": "",
        ".gitignore": "__pycache__/\n*.pyc\n.pytest_cache/\n",
        "conftest.py": "import sys\nfrom pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n",
        "tests/test_regression.py": "def test_untouched_regression():\n    assert 2 + 2 == 4\n",
        **(
            ANALYSIS_FILES
            if case.mode == "analysis"
            else {
                (name if case.mode == "security" else "src/" + name): source
                for name, source in case.initial.items()
            }
        ),
    }
    for name, source in files.items():
        _write(repo / name, source)
    if case.id == "C01":
        _write(
            repo / "tests/test_regression.py",
            "from src.app import clamp\n\n\n"
            "def test_existing_positive_value():\n    assert clamp(5) == 5\n",
        )
    for args in (
        ("init", "-q"),
        ("config", "user.name", "Sprout Eval"),
        ("config", "user.email", "eval@invalid.example"),
        ("config", "core.hooksPath", str(directory / "disabled-hooks")),
        ("config", "commit.gpgsign", "false"),
        ("add", "-A"),
        ("commit", "-qm", "fixture baseline"),
    ):
        _git(repo, *args)
    return repo


def _runtime(directory: Path, model: ScriptedModel):
    from Sprout.agent.loop import AgentLoop
    from Sprout.agent.planner import DirectPlanner
    from Sprout.agent.router import AgentRouter
    from Sprout.config.loader import default_settings
    from Sprout.runtime.factory import create_runtime

    settings = default_settings()
    settings.model.provider = "echo"
    settings.skills_dir = str(directory / "skills")
    for slot in ("operational", "metadata", "session"):
        setattr(settings.storage, slot, f"sqlite:///{(directory / (slot + '.db')).as_posix()}")
    settings.storage.knowledge = "memory://"
    settings.storage.observations.enabled = False
    settings.storage.trajectory_dir = str(directory / "trajectory")
    settings.storage.blobs_dir = str(directory / "blobs")
    settings.storage.project_root = str(directory / "projects")
    settings.security.audit.path = str(directory / "audit.jsonl")
    runtime = create_runtime(settings)
    runtime.models.register(model, default=True)
    original = runtime.agents.get(runtime.default_agent)
    runtime.register_agent(
        runtime.default_agent,
        AgentLoop(
            model=model,
            executor=original.executor,
            max_steps=original.max_steps,
            planner=DirectPlanner(),
        ),
        default=True,
    )
    runtime.set_router(AgentRouter(runtime.agents, default=runtime.default_agent))
    return runtime


def _oracle(directory: Path, repo: Path, case: EvalCase) -> dict:
    script = directory / "independent_oracle.py"
    _write(script, ORACLE)
    # No inherited API credentials or project pytest plugins. Windows needs SystemRoot.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}
    }
    result = subprocess.run(
        [sys.executable, "-I", str(script), str(repo), json.dumps(case.acceptance)],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"Independent oracle runner error: {result.stderr[:1000]}")
    return {
        "exit_code": result.returncode,
        "checks": json.loads(result.stdout),
        "oracle_sha256": sha256(ORACLE.encode()).hexdigest(),
        "revision": _git(repo, "rev-parse", "HEAD"),
    }


async def _drive(runtime, task_id: str, *, stop_at_proposal=False):
    """Simulate explicit operator decisions for this fixture task only."""
    for _ in range(20):
        task = await runtime.get_task(task_id)
        if task.status.value in {"completed", "failed", "cancelled"}:
            return task
        proposals = await runtime.list_change_proposals(task_id)
        if stop_at_proposal and any(item.status.value == "pending" for item in proposals):
            return task
        pending = [item for item in await runtime.pending_approvals() if item.task_id == task_id]
        if pending:
            # Public decide already resumes; don't separately replay the graph.
            await runtime.decide_approval(pending[0].id, True, decided_by="eval-fixture-operator")
            continue
        proposal = next((item for item in proposals if item.status.value == "pending"), None)
        if proposal:
            await runtime.approve_change_proposal(proposal.id, decided_by="eval-fixture-operator")
            continue
        raise RuntimeError(f"Fixture has no actionable gate: {task.status.value}")
    raise RuntimeError("Fixture exceeded bounded approval/resume rounds")


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


async def _analysis(runtime, workspace, case, directory):
    from Sprout.workspace.serialization import to_jsonable

    first = await runtime.analyze_workspace(workspace.id)
    _json(directory / "analysis.json", to_jsonable(first))
    if case.id == "A01":
        _require(
            any(node.qualified_name == "clamp" for node in first.graph.nodes),
            "clamp symbol missing",
        )
        _require(
            any(edge.relation == "imports" and edge.evidence for edge in first.graph.edges),
            "evidenced import edge missing",
        )
    elif case.id == "A02":
        mounted = [
            item for item in first.knowledge.items if item.kind == "interface_mounted_endpoint"
        ]
        _require(
            {item.details["mounted_path"] for item in mounted} == {"/v1/users/{id}"},
            "mounted path differs from ground truth",
        )
        _require(all(not item.details["deployed"] for item in mounted), "deployed falsely claimed")
        _require(
            any(item.kind == "analysis_warning" for item in first.knowledge.items),
            "dynamic prefix must remain unresolved",
        )
    else:
        tables = [item for item in first.knowledge.items if item.kind == "database_table"]
        _require(
            {item.details["table"] for item in tables} == {"users", "teams"},
            "ORM/SQL table ground truth mismatch",
        )
        _require(
            all(not item.details["complete"] for item in tables), "live schema falsely claimed"
        )
        _require(all(item.evidence_ids for item in tables), "table source evidence missing")
    await runtime.stop()
    restarted = _runtime(directory, ScriptedModel(case))
    try:
        restored = await restarted.analyze_workspace(workspace.id)
        _require(
            first.graph == restored.graph and first.knowledge == restored.knowledge,
            "persisted analysis differs after restart",
        )
    finally:
        await restarted.stop()
    return {"analysis_verified": True, "task_status": None, "verification": None}


async def _coding(runtime, workspace, case, directory, model):
    repo = workspace.root
    baseline = await asyncio.to_thread(_oracle, directory, repo, case)
    _json(directory / "baseline.json", baseline)
    _require(baseline["exit_code"] == 1, "baseline must fail before the fixture implementation")
    task = await runtime.create_task_planned(workspace.id, case.instruction, source="cli")
    await runtime.execute(task)
    if case.mode == "restart":
        waiting = await runtime.get_task(task.id)
        _require(waiting.status.value == "waiting_approval", "restart fixture never waited")
        await runtime.stop()
        runtime = _runtime(directory, model)
    try:
        current = await _drive(runtime, task.id, stop_at_proposal=case.mode == "force")
        if case.mode == "force":
            proposals = await runtime.list_change_proposals(task.id)
            proposal = next(item for item in proposals if item.status.value == "pending")
            await runtime.approve_change_proposal(proposal.id, decided_by="eval-fixture-operator")
            applied = await runtime.apply_change_proposal(
                proposal.id,
                force=True,
                override_reason="Eval fixture: deliberate failed check waiver",
                decided_by="eval-fixture-operator",
            )
            _require(applied.applied, f"force application refused: {applied.reason}")
            current = await runtime.get_task(task.id)
        report = await runtime.get_verification_report(task.id)
        proposals = await runtime.list_change_proposals(task.id)
        nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
        approvals = [item for item in await runtime.list_approvals() if item.task_id == task.id]
        _json(directory / "task.json", asdict(current))
        _json(directory / "nodes.json", [asdict(item) for item in nodes])
        _json(directory / "proposals.json", [asdict(item) for item in proposals])
        _json(directory / "approvals.json", [asdict(item) for item in approvals])
        _json(directory / "verification.json", report)
        after = await asyncio.to_thread(_oracle, directory, repo, case)
        _json(directory / "independent_result.json", after)
        if case.expected == "completed":
            _require(
                current.status.value == "completed" and report["verified"],
                "task not verified complete",
            )
            _require(after["exit_code"] == 0, "independent output oracle failed")
            _require(after["revision"] != baseline["revision"], "no applied commit")
            _require(
                any(
                    check["name"].startswith("poc: ") and check["passed"]
                    for check in report["checks"]
                ),
                "minimal demo did not pass",
            )
        else:
            _require(
                current.status.value != "completed" and not report["verified"],
                "false positive completion",
            )
            _require(
                after["exit_code"] == 1, "negative fixture unexpectedly passed independent oracle"
            )
            if case.mode == "force":
                _require(report["overridden"], "force waiver was not recorded")
            else:
                _require(current.status.value == "failed", "incorrect implementation not failed")
                _require(after["revision"] == baseline["revision"], "failed patch was applied")
        if case.mode == "restart":
            revision = after["revision"]
            for _ in range(2):
                try:
                    await runtime.resume_task(await runtime.get_task(task.id))
                except ValueError:
                    pass  # Terminal resume may refuse; neither refusal nor no-op may reapply.
            _require(_git(repo, "rev-parse", "HEAD") == revision, "repeated resume reapplied patch")
        # Generated acceptance/demo files are allowed; edits to existing regression tests are not.
        changed = set(_git(repo, "diff", "--name-only", baseline["revision"], "HEAD").splitlines())
        _require("tests/test_regression.py" not in changed, "existing regression test changed")
        allowed = {"src/" + path for path in case.edits}
        for node in nodes:
            output = node.metadata.get("output") or {}
            if node.type.value == "test_design":
                allowed.update(
                    str(output[key]) for key in ("test_path", "demo_path") if key in output
                )
        _require(not changed - allowed, f"patch exceeds fixture scope: {sorted(changed - allowed)}")
        return {
            "task_id": task.id,
            "task_status": current.status.value,
            "verification": report,
            "baseline_valid": True,
            "independent_passed": after["exit_code"] == 0,
            "model_calls": model.calls,
            "applied_revision": after["revision"],
        }
    finally:
        await runtime.stop()


async def _security(runtime, workspace, case, directory):
    from Sprout.execution.file_broker import FileBroker
    from Sprout.execution.models import SandboxRef
    from Sprout.security.access import AccessDecision, PolicyDecision

    before = _git(workspace.root, "rev-parse", "HEAD")
    task = await runtime.create_task_planned(
        workspace.id, case.instruction, source="cli", execution_mode="read_only"
    )
    await runtime.execute(task)
    _require((workspace.root / "app.py").read_text() == "value = 1\n", "read-only source changed")
    _require(not await runtime.list_change_proposals(task.id), "read-only produced a proposal")
    _require(_git(workspace.root, "rev-parse", "HEAD") == before, "read-only created a commit")

    class Deny:
        def decide(self, request):
            return PolicyDecision(AccessDecision.DENY, reason="Eval deletion denial")

    denied = await FileBroker(Deny()).delete(
        SandboxRef(id="fixture", kind="fixture", root=workspace.root), "app.py"
    )
    _require(not denied.wrote and (workspace.root / "app.py").exists(), "denied deletion executed")
    from Sprout.evaluation.fixtures import GOOD, NORMAL

    delete_case = replace(
        case,
        mode="delete",
        initial={"obsolete.py": "value = 1\n"},
        edits={"app.py": GOOD},
        acceptance=NORMAL,
        instruction="Implement src.app.clamp and remove src/obsolete.py.",
    )
    delete_directory = directory / "delete-flow"
    repo = await asyncio.to_thread(_repo, delete_directory, delete_case)
    delete_runtime = _runtime(delete_directory, ScriptedModel(delete_case))
    try:
        opened = await delete_runtime.open_workspace(repo)
        delete_task = await delete_runtime.create_task_planned(
            opened.id, delete_case.instruction, source="cli"
        )
        await delete_runtime.execute(delete_task)
        deletion = None
        for _ in range(20):
            pending = [
                item
                for item in await delete_runtime.pending_approvals()
                if item.task_id == delete_task.id
            ]
            deletion = next((item for item in pending if item.tool == "file_delete"), None)
            if deletion is not None:
                break
            _require(bool(pending), "deletion approval was never reached")
            await delete_runtime.decide_approval(
                pending[0].id, True, decided_by="eval-fixture-operator"
            )
        _require(deletion is not None, "deletion fixture exceeded approval rounds")
        await delete_runtime.decide_approval(deletion.id, False, decided_by="eval-fixture-operator")
        parked = await delete_runtime.get_task(delete_task.id)
        _require(
            parked.status.value == "waiting_approval", "denied deletion did not remain waiting"
        )
        _require((repo / "src/obsolete.py").exists(), "rejected deletion changed the project")
        _json(delete_directory / "denied-task.json", asdict(parked))
        await delete_runtime.cancel_task(delete_task.id)
    finally:
        await delete_runtime.stop()
    return {
        "task_id": task.id,
        "task_status": (await runtime.get_task(task.id)).status.value,
        "verification": None,
        "security_violations": 0,
        "scope": "read-only injection; denied deletion stays waiting until explicit cancellation",
    }


async def run_suite(output: Path | None = None, *, case_ids: tuple[str, ...] = ()) -> dict:
    """Create a new evidence directory; never evaluate caller-supplied project code."""
    selected = [case for case in CASES if not case_ids or case.id in case_ids]
    unknown = set(case_ids) - {case.id for case in CASES}
    if unknown:
        raise ValueError(f"Unknown eval cases: {sorted(unknown)}")
    if os.getenv("TEMPORAL_HOST"):
        raise ValueError(
            "Offline eval requires TEMPORAL_HOST to be unset; no remote workflow is submitted"
        )
    if importlib.util.find_spec("pytest") is None:
        raise ValueError(
            "Offline eval needs the dev pytest dependency; "
            "dependencies are never installed automatically"
        )
    for executable in ("git", "ruff", "python", "pytest"):
        if shutil.which(executable) is None:
            raise ValueError(
                f"Offline eval requires {executable} on PATH; no automatic installation"
            )
    parent = output.resolve() if output else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="sprout-eval-", dir=parent))
    results = []
    started = time.monotonic()
    for case in selected:
        directory = root / case.id
        directory.mkdir()
        _json(directory / "fixture.json", asdict(case))
        case_start = time.monotonic()
        runtime = None
        result = {
            "id": case.id,
            "title": case.title,
            "scenario_passed": False,
            "failure_stage": "setup",
            "cost_usd": 0.0,
        }
        try:
            repo = await asyncio.to_thread(_repo, directory, case)
            model = ScriptedModel(case)
            runtime = _runtime(directory, model)
            workspace = await runtime.open_workspace(repo)
            result["failure_stage"] = case.mode
            if case.mode == "analysis":
                evidence = await _analysis(runtime, workspace, case, directory)
            elif case.mode == "security":
                evidence = await _security(runtime, workspace, case, directory)
            else:
                evidence = await _coding(runtime, workspace, case, directory, model)
            result.update(evidence, scenario_passed=True, failure_stage=None)
        except Exception as exc:
            result.update(error=f"{type(exc).__name__}: {exc}")
            verification_path = directory / "verification.json"
            if verification_path.is_file():
                result["verification"] = json.loads(verification_path.read_text(encoding="utf-8"))
                result["failure_stage"] = "verification"
        finally:
            if runtime is not None:
                try:
                    await runtime.stop()
                except Exception as exc:
                    result.update(
                        scenario_passed=False,
                        failure_stage="cleanup",
                        cleanup_error=f"{type(exc).__name__}: {exc}",
                    )
        result["duration_ms"] = (time.monotonic() - case_start) * 1000
        result["evidence_dir"] = str(directory)
        _json(directory / "result.json", result)
        results.append(result)
        _json(root / "partial-results.json", results)
    report = {
        "version": 1,
        "fixture_version": 1,
        "provider": ScriptedModel.name,
        "fixture_sha256": sha256(
            json.dumps([asdict(case) for case in CASES], sort_keys=True).encode()
        ).hexdigest(),
        "source_sha256": sha256(
            b"".join(
                path.relative_to(Path(__file__).parents[1]).as_posix().encode() + path.read_bytes()
                for path in sorted(Path(__file__).parents[1].rglob("*.py"))
                if "tests" not in path.relative_to(Path(__file__).parents[1]).parts
            )
        ).hexdigest(),
        "model_quality_measured": False,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "executables": {name: shutil.which(name) for name in ("git", "python", "pytest", "ruff")},
        "oracle_python": sys.executable,
        "evidence_dir": str(root),
        "total": len(results),
        "scenario_passed": sum(item["scenario_passed"] for item in results),
        "valid_executions": sum("task_status" in item for item in results),
        "baseline_invalid": sum("baseline must fail" in item.get("error", "") for item in results),
        "total_cost_usd": 0.0,
        "average_duration_ms": sum(item["duration_ms"] for item in results) / len(results),
        "verification_failure_codes": dict(
            Counter(
                code
                for item in results
                for code in (item.get("verification") or {}).get("failure_codes", [])
            )
        ),
        "completed_code_tasks": sum(
            item.get("task_status") == "completed"
            and bool(item.get("verification", {}).get("verified"))
            for item in results
            if item.get("verification")
        ),
        "failure_stages": dict(
            Counter(item["failure_stage"] for item in results if not item["scenario_passed"])
        ),
        "duration_ms": (time.monotonic() - started) * 1000,
        "results": results,
    }
    _json(root / "report.json", report)
    return report
