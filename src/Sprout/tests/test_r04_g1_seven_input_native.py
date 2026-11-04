"""Seven exact stage-C inputs; real Runtime/Receipt/Git, controlled model only."""
import asyncio
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from Sprout.config.settings import SecuritySettings
from Sprout.execution import file_access, git_s1
from Sprout.security.access import AccessDecision
from Sprout.security.audit import SecurityAuditLog
from Sprout.security.layered_policy import LayeredPolicyEngine, PolicyLayer, PolicyRule
from Sprout.tests.test_r04_e_apply_native import FixturePolicy, prepare_apply_fixture

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Explicit Windows native entry required",
)


@pytest.mark.parametrize("iteration", [1, 2, "view_changed", "source_changed", "object_denied"])
async def test_seven_input_git_review(tmp_path, monkeypatch, iteration):
    fixture = tmp_path / "stage" / "c" / "seven" / "input"
    fixture.mkdir(parents=True)
    ctx = await prepare_apply_fixture(fixture, monkeypatch, "cli", seven_inputs=True)
    runtime, store = ctx["runtime"], ctx["storage"].metadata
    task = replace(ctx["task"], delegation_scope=replace(
        ctx["task"].delegation_scope, allowed_paths=tuple(ctx["files"])))
    await store.save_task(task)
    audit = SecurityAuditLog(tmp_path / "security.jsonl")
    policy = LayeredPolicyEngine.from_settings(
        SecuritySettings(), base=FixturePolicy(), audit=audit)
    runtime._policy_engine = runtime._file_broker._policy = runtime._process_broker._policy = policy
    runtime._git_broker._policy = policy
    exports = Path(tempfile.mkdtemp(prefix="sprout-g1-seven-"))
    await runtime.configure_local_git_review(task.id, task.actor, executable=ctx["git"],
        executable_sha256="cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96",
        export_parent=exports)
    metrics, processes = {}, []
    enabled = False
    active_binding = None
    tampered = False
    object_io = False
    object_selections = []

    def record(label, elapsed):
        entry = metrics.setdefault(label, {"calls": 0, "seconds": 0, "max_seconds": 0})
        entry["calls"] += 1
        entry["seconds"] += elapsed
        entry["max_seconds"] = max(entry["max_seconds"], elapsed)

    def sync_probe(obj, name, label):
        original = getattr(obj, name)
        def observed(*args, **kwargs):
            nonlocal tampered, object_io
            prior_io = object_io
            if enabled and label == "snapshot":
                object_io = Path(args[1]).as_posix().startswith("objects/")
            if enabled and label == "policy" and object_io:
                object_selections.append(list(args[1]))
            if (enabled and label == "object_file" and iteration == "object_denied"
                    and args[1] == "src/app.py" and not tampered):
                policy._layers = (*policy.layers, PolicyLayer("workspace", (PolicyRule(
                    action="git.diff", path_glob=str(active_binding.sandbox.root / "src/app.py"),
                    decision=AccessDecision.DENY, id="fixture-object-deny"),)))
                tampered = True
            if (enabled and label == "source" and iteration == "source_changed"
                    and metrics.get("source", {}).get("calls") == 1 and not tampered):
                ref = (active_binding.metadata / "HEAD").read_text().strip()
                assert ref.startswith("ref: ")
                target = active_binding.common / ref[5:]
                target.write_bytes(b"0" * 40 + b"\n")
                tampered = True
            start = time.monotonic()
            try:
                return original(*args, **kwargs)
            finally:
                if enabled:
                    record(label, time.monotonic() - start)
                object_io = prior_io
        monkeypatch.setattr(obj, name, observed)

    def async_probe(obj, name, label):
        original = getattr(obj, name)
        async def observed(*args, **kwargs):
            nonlocal tampered
            if (enabled and label == "command_with_checks" and iteration == "view_changed"
                    and args[2][0] == "diff" and not tampered):
                (args[1] / "current/src/app.py").write_bytes(b"tampered\n")
                tampered = True
            start = time.monotonic()
            try:
                return await original(*args, **kwargs)
            finally:
                if enabled:
                    record(label, time.monotonic() - start)
        monkeypatch.setattr(obj, name, observed)

    for obj, name, label in (
        (git_s1, "_snapshot", "snapshot"), (git_s1, "_source", "source"),
        (git_s1, "_write_view_file", "view_write"),
        (git_s1._LooseObjects, "object", "objects"),
        (git_s1._LooseObjects, "file", "object_file"),
        (runtime._git_broker, "_check_review_policy", "policy")):
        sync_probe(obj, name, label)
    for obj, name, label in (
        (git_s1, "_check_review_files", "view_check"),
        (git_s1, "_review_command", "command_with_checks"),
        (runtime, "_git_review_record", "runtime_authority"),
        (runtime._git_broker, "_authorize_review", "broker_authority"),
        (file_access, "directory_entries", "directory_listing")):
        async_probe(obj, name, label)
    sync_probe(audit, "record_decision", "audit")
    installation = git_s1._pin_installation

    @contextmanager
    def pinned(*args, **kwargs):
        context = installation(*args, **kwargs)
        started = time.monotonic()
        value = context.__enter__()
        if enabled:
            record("installation_enter", time.monotonic() - started)
        try:
            yield value
        finally:
            started = time.monotonic()
            context.__exit__(None, None, None)
            if enabled:
                record("installation_exit", time.monotonic() - started)

    monkeypatch.setattr(git_s1, "_pin_installation", pinned)
    spawn = asyncio.create_subprocess_exec

    async def spawned(*argv, **kwargs):
        started = time.monotonic()
        process = await spawn(*argv, **kwargs)
        if enabled:
            processes.append((process, {"pid": process.pid, "argv": list(argv),
                "cwd": str(kwargs.get("cwd")), "spawn_seconds": time.monotonic() - started}))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawned)
    review = git_s1.run_review
    measured = {}

    async def reviewing(*args, **kwargs):
        nonlocal enabled, active_binding
        active_binding = args[0]
        enabled = True
        started = time.monotonic()
        try:
            result = await review(*args, **kwargs)
            measured["review"] = json.loads(result.output)
            return result
        finally:
            measured["seconds"] = time.monotonic() - started
            enabled = False

    monkeypatch.setattr(git_s1, "run_review", reviewing)
    try:
        async with asyncio.timeout(240):
            await runtime.execute(task)
        nodes = await store.list_execution_nodes(task.id)
        proposals = await store.list_change_proposals(task.id)
        measured["nodes"] = [{"type": n.type.value, "status": n.status.value,
                              "metadata": n.metadata} for n in nodes]
        assert len(ctx["files"]) == 7
        if isinstance(iteration, str):
            assert tampered and not proposals
            approval = next(n for n in nodes if n.type.value == "approval")
            assert approval.metadata["execution_guard"]["state"] == "uncertain"
            expected = {"view_changed": "git_view_content_changed",
                        "source_changed": "git_baseline_changed",
                        "object_denied": "fixture-object-deny"}[iteration]
            assert expected in str(approval.metadata)
            return
        assert len(proposals) == 1, measured
        evidence = measured["review"]
        assert evidence["changed_paths"] == ["src/app.py"]
        assert evidence["coverage"]["status"] == "partial"
        assert len(evidence["commands"]) == 5
        assert [c["exit_code"] for c in evidence["commands"]] == [0, 1, 0, 1, 0]
        assert "return 0 if value < 0 else value" in evidence["patches"]["tracked"]
        assert evidence["budgets"]["elapsed_seconds"] < git_s1.REVIEW_TOTAL_SECONDS
        assert metrics["view_check"]["calls"] == 10
        assert metrics["source"]["calls"] == 2
        assert object_selections and all(len(paths) == 1 and paths[0] in ctx["files"]
                                         for paths in object_selections)
        assert all(p.returncode is not None for p, _ in processes)
        assert (ctx["root"] / "src/app.py").read_text() == ctx["old"]
        measured["proposal_id"] = proposals[0].id
    finally:
        measured.update(iteration=iteration, task_id=task.id, metrics=metrics,
            object_selections=object_selections,
            input_sha256={p: hashlib.sha256(text.encode()).hexdigest()
                          for p, text in ctx["files"].items()},
            processes=[{**r, "returncode": p.returncode} for p, r in processes])
        measured["diagnostics"] = [json.loads(p.read_text())
                                  for p in exports.glob("git-review-diagnostic-*.json")]
        assert all(not d["unresolved_processes"] for d in measured["diagnostics"])
        (tmp_path / "SEVEN_INPUT_MEASUREMENT.json").write_text(
            json.dumps(measured, default=str, indent=2), encoding="utf-8")
        await runtime.stop()
        store.close()
