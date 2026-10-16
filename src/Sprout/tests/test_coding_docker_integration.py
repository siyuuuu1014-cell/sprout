"""Opt-in real coding/import/JSON checks, no model or production task execution."""

import json
import os
import re
import subprocess
from dataclasses import replace
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

import pytest

from Sprout.execution.coding_session import CodingNodeSession
from Sprout.execution.docker_verification import validate_image
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.models import SandboxRef
from Sprout.execution.process_broker import ProcessBroker
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus
from Sprout.workspace.models import Workspace

IMAGE = os.getenv("SPROUT_CODING_TEST_IMAGE", "")
pytestmark = pytest.mark.skipif(not IMAGE, reason="explicit pinned coding test image required")


class FixturePolicy:
    """Explicit test-only authorization, not a production approval replacement."""

    def decide(self, request):
        if request.action == ActionType.FILE_READ:
            return PolicyDecision(AccessDecision.ALLOW, "explicit fixture read")
        if request.action == ActionType.PROCESS_RUN:
            argv = tuple(request.arguments.get("command", ()))
            permitted = argv[:3] == ("docker", "image", "inspect") or argv[:2] == ("docker", "run")
            return PolicyDecision(AccessDecision.ALLOW if permitted else AccessDecision.DENY,
                                  "explicit opt-in coding fixture")
        return PolicyDecision(AccessDecision.SANDBOX_ONLY, "fixture sandbox files only")


def inspect_and_remove_owned_stopped(name, evidence):
    if not re.fullmatch(r"sprout-coding-[0-9a-f]{32}", name):
        raise AssertionError("Recorded test container name required")
    process = subprocess.run(["docker", "inspect", name], capture_output=True,
                             text=True, timeout=15)
    if process.returncode:
        return  # An unconfirmed launch may not have created a container.
    info = json.loads(process.stdout)[0]
    assert info["Config"]["Labels"].get("sprout.role") == "isolated-coding"
    assert info["State"]["Running"] is False, "Running/unknown container retained"
    (evidence / (name + ".inspection.json")).write_text(
        json.dumps(info, indent=2), encoding="utf-8")
    subprocess.run(["docker", "rm", name], capture_output=True, check=True, timeout=15)
    return info


@pytest.mark.parametrize("mode", ["command_only", "command_pytest", "model_files_pytest"])
async def test_real_container_modification_import_and_host_json_checks(tmp_path, mode):
    run_pytest = mode != "command_only"
    validate_image(IMAGE)
    original, target, evidence = (tmp_path / name for name in ("original", "target", "evidence"))
    for root in (original, target, evidence):
        root.mkdir()
    (original / "unchanged.txt").write_text("original untouched")
    (target / "app.py").write_text("def add(a, b): return 0\n", encoding="utf-8", newline="")
    (target / "tests").mkdir()
    (target / "tests/oracle.py").write_text("frozen-host-only", encoding="utf-8")
    store = open_metadata_store(str(evidence / "metadata.db"))
    task = Task(status=TaskStatus.RUNNING, metadata={"execution_mode": "code"})
    await store.save_task(task)
    workspace = Workspace(id="fixture", root=target)
    policy = FixturePolicy()
    broker = ProcessBroker(policy)

    class Runner:
        async def run_task_process(self, task_id, command, **kwargs):
            return await broker.run(workspace, command, task_id=task_id, **kwargs)

        async def authorize_task_process(self, task_id, command):
            return await broker.run(workspace, command, task_id=task_id, preflight_only=True)

    runner, file_broker = Runner(), FileBroker(policy)
    sandbox = SandboxRef("fixture", "fixture", target)
    identities, durations = [], []
    try:
        coding = ExecutionNode(id=task.id + ":agent", task_id=task.id, type=NodeType.AGENT)
        await store.save_execution_node(coding)
        assert await store.claim_execution_node(coding, "coding-owner", "")
        coding = await store.get_execution_node(coding.id)
        session = CodingNodeSession(store, runner, file_broker,
            evidence=evidence, original=original,
            image=IMAGE, oracle_paths=("tests",), sandbox=sandbox, task=task, node=coding)
        identities.append(str(uuid5(NAMESPACE_URL, coding.id + ":coding:evaluation-0")))
        started = perf_counter()
        script = ("from pathlib import Path; "
                  "Path('app.py').write_text('def add(a, b): return a+b\\n')")
        if run_pytest:
            generated = ("from app import add\n"
                         "def test_positive(): assert add(2, 3) == 5\n"
                         "def test_negative(): assert add(-4, 1) == -3\n"
                         "def test_zero(): assert add(0, 0) == 0\n")
            script += (f"; Path('test_generated.py').write_text({generated!r}); "
                       "import subprocess, sys; "
                       "sys.exit(subprocess.call([sys.executable, '-m', 'pytest', '-q']))")
        if mode == "model_files_pytest":
            from Sprout.execution.sandbox_tool import SandboxWriteTool

            writer = SandboxWriteTool(sandbox, file_broker, task_id=task.id)
            assert (await writer.invoke({"path": "app.py",
                "content": "def add(a, b): return a+b\n"})).ok
            assert (await writer.invoke({"path": "test_generated.py", "content": generated})).ok
            script = ("import subprocess, sys; "
                      "sys.exit(subprocess.call([sys.executable, '-m', 'pytest', '-q']))")
        changed = await session.execute_command(("python", "-c", script),
            cwd=target, command_slot="evaluation-0")
        durations.append((perf_counter() - started) * 1000)
        assert changed["imported"] is True
        assert (target / "app.py").read_text() == "def add(a, b): return a+b\n"
        if run_pytest:
            # Inspect the original export without preparing/replacing a settled attempt.
            from Sprout.execution.coding_container import coding_node_directory_name

            exported = evidence / coding_node_directory_name(coding.id) / "coding-input"
            assert (exported / ".pytest_cache/v/cache/nodeids").is_file()
            assert not (target / ".pytest_cache").exists()
            assert (target / "test_generated.py").read_text() == generated
            assert "3 passed" in changed["process"].stdout
            imported = await store.get_coding_execution(str(uuid5(
                NAMESPACE_URL, coding.id + ":coding-import:evaluation-0-import")))
            assert imported["state"] == "completed"
            expected = [] if mode == "model_files_pytest" else ["app.py", "test_generated.py"]
            assert imported["result"]["changed_paths"] == expected
        assert await store.finish_execution_node(replace(coding, status=NodeStatus.COMPLETED),
                                                  "coding-owner", "completed")
        evaluation = ExecutionNode(id=task.id + ":evaluation", task_id=task.id,
                                   type=NodeType.EVALUATION)
        await store.save_execution_node(evaluation)
        assert await store.claim_execution_node(evaluation, "evaluation-owner", "")
        evaluation = await store.get_execution_node(evaluation.id)
        session = CodingNodeSession(store, runner, file_broker,
            evidence=evidence, original=original,
            image=IMAGE, oracle_paths=("tests",), sandbox=sandbox, task=task, node=evaluation)
        for slot in ("evaluation-acceptance-0", "evaluation-regression-0", "evaluation-poc-0"):
            identities.append(str(uuid5(NAMESPACE_URL, evaluation.id + ":coding:" + slot)))
        started = perf_counter()
        measured = await session.verify(
            acceptance_spec={"cases": [{"id": "normal", "target": "app:add", "args": [2, 3],
                                         "expected_output": 5}]},
            regression_spec={"cases": [{"id": "zero", "target": "app:add", "args": [-1, 1],
                                         "expected_output": 0}]})
        durations.append((perf_counter() - started) * 1000)
        assert measured["verified"] is True and measured["validation_committed"] is False
        assert (target / "tests/oracle.py").read_text() == "frozen-host-only"
        assert (original / "unchanged.txt").read_text() == "original untouched"
    finally:
        records = [await store.get_coding_execution(identity) for identity in identities]
        process_ms = []
        try:
            for record in records:
                if not record:
                    continue
                if record["state"] != "completed":
                    continue  # Unknown executions retain their containers for manual inspection.
                command = record["command"]
                info = inspect_and_remove_owned_stopped(
                    command[command.index("--name") + 1], evidence)
                if info is not None:
                    assert info["HostConfig"]["NetworkMode"] == "none"
                    assert info["HostConfig"]["ReadonlyRootfs"] is True
                    assert info["HostConfig"]["Memory"] == 512 * 1024 * 1024
                    assert info["HostConfig"]["NanoCpus"] == 1_000_000_000
                    assert info["Config"]["User"] == "65532:65532"
                if record.get("result"):
                    process_ms.append(record["result"].get("duration_ms"))
            (evidence / "coding-smoke-metrics.json").write_text(json.dumps({
                "phase_ms": durations, "process_ms": process_ms,
                "record_count": sum(record is not None for record in records),
                "scope": "one opt-in sample; no cold/warm comparison or production model",
                "passed": bool(locals().get("measured", {}).get("verified")),
            }, indent=2), encoding="utf-8")
        finally:
            store.close()
