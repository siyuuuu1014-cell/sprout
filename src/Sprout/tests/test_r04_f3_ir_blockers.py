"""IR-1/IR-2 control-plane regressions; no native Docker or target execution."""
import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.models import ProcessResult
from Sprout.execution.models import TestResult as Check
from Sprout.execution.process_broker import ProcessBroker
from Sprout.execution.verification import build_verification_report, proposal_verification_report
from Sprout.execution.verification_manifest import VerificationManifestV1, _VerificationExecution
from Sprout.execution.verification_run import DockerVerificationRun, _UnverifiedPytest
from Sprout.tests.test_r04_f3_adapter import inspection, observation, plan
from Sprout.tests.test_r04_f3_manifest import execution as execution
from Sprout.tests.test_r04_f3_manifest import f3 as f3
from Sprout.tests.test_r04_f3_manifest import rig as rig


def pytest_plan(trusted=False):
    value = plan()
    request = value["request"]
    request.update(argv=["pytest", "-q"], pytest_observer_sha="9" * 64,
        pytest_contract={"files": ["tests/frozen.py"], "plugins": [],
                         "ini_file": None, "configs": {}})
    child = {"nonce": request["nonce"], "exit_code": 0, "trusted_execution": trusted,
        "collected": ["tests/frozen.py::never_ran"], "collection_errors": [],
        "configuration": {"plugins": [], "relative_config": None},
        "runner": {"probe_hash": request["pytest_observer_sha"]},
        "reports": [{"nodeid": "tests/frozen.py::never_ran", "phase": "call",
                     "outcome": "passed", "wasxfail": False}]}
    parent = json.loads(observation(value).split(" ", 2)[2])
    parent["stdout"] = "SPROUT_PYTEST_REPORT " + request["nonce"] + " " + json.dumps(child)
    output = "SPROUT_COMMAND_REPORT " + request["nonce"] + " " + json.dumps(parent)
    return value, output


@pytest.mark.parametrize("trusted", [False, True])
def test_correct_public_fields_and_self_claimed_trust_never_prove_pytest(trusted):
    value, output = pytest_plan(trusted)
    with pytest.raises(_UnverifiedPytest) as caught:
        DockerVerificationRun.command_observation(value, output, 0, inspection(value))
    assert caught.value.evidence["outcome"] == "unverified"
    assert not caught.value.evidence["passed"]


def test_forged_exit_code_is_not_unverified_success():
    value, output = pytest_plan()
    with pytest.raises(ValueError, match="container_not_confirmed"):
        DockerVerificationRun.command_observation(value, output, 1, inspection(value))


@pytest.mark.parametrize("lost_create_response", [False, True])
async def test_owned_started_and_stopped_pytest_preserves_unverified_process_evidence(
    lost_create_response,
):
    value, output = pytest_plan()
    native, calls = {}, []
    metadata = SimpleNamespace(claim_verification_execution=AsyncMock(return_value=True),
        finish_verification_execution=AsyncMock(return_value=True))
    scope = SimpleNamespace(task=SimpleNamespace(id="task"), node=SimpleNamespace(id="node"),
        runtime=SimpleNamespace(_verification_preparation_record=AsyncMock()))
    broker = SimpleNamespace()
    key = _VerificationExecution(broker, scope, object(), {}, "pytest", "approval",
                                 asyncio.current_task())
    broker._verification_execution_record = AsyncMock(return_value=(native, value,
        {"task_id": "task", "owner": "owner"}))
    async def step(binding, action, cid):
        calls.append(action)
        if action == "create":
            native["grant_consumed"] = True  # substituted Broker; never a real grant
            return ProcessResult(("docker",), 0,
                "" if lost_create_response else "1" * 64, started=True)
        if action == "start":
            return ProcessResult(("docker",), 0, output, started=True)
        return ProcessResult(("docker",), 0, json.dumps([inspection(value,
            status="exited" if "start" in calls else "created")]), started=True)
    broker._verification_step = step
    broker._verification_cleanup_task = lambda key, task: native.update(cleanup_task=task)
    result = await DockerVerificationRun(metadata, None).execute_prepared(key)
    if lost_create_response:
        assert result["outcome"] == "UNKNOWN" and not result["executed"]
        assert result["container_id"] is None and result["manual_intervention_required"]
        assert calls == ["create"]  # no name-based adoption or start
        assert native["manual_intervention_required"]
        assert metadata.finish_verification_execution.await_args.args[3][
            "manual_intervention_required"]
        return
    assert result["executed"] and not result["passed"]
    assert result["outcome"] == "unverified" and result["exit_code"] == 0
    assert calls == ["create", "inspect", "start", "inspect"]
    assert metadata.finish_verification_execution.await_args.args[2] == "completed"


@pytest.mark.parametrize("cancel", [False, True])
async def test_claim_io_remains_owned_and_late_success_never_creates(cancel, monkeypatch):
    native, entered, release = {}, asyncio.Event(), asyncio.Event()
    async def worker():
        entered.set()
        await release.wait()
        return True
    io = asyncio.create_task(worker())
    async def claim(record):
        return await asyncio.shield(io)  # models an uncancellable SQLite thread
    metadata = SimpleNamespace(claim_verification_execution=claim)
    scope = SimpleNamespace(task=SimpleNamespace(id="task"), node=SimpleNamespace(id="node"))
    broker = SimpleNamespace(_verification_execution_record=AsyncMock(return_value=(
        native, plan(), {"owner": "owner"})), _verification_step=AsyncMock())
    key = _VerificationExecution(broker, scope, object(), {}, "demo", "approval",
                                 asyncio.current_task())
    bounded = ProcessBroker._verification_wait
    async def short(record, operation, seconds, **kwargs):
        return await bounded(record, operation, min(seconds, .02), **kwargs)
    monkeypatch.setattr(ProcessBroker, "_verification_wait", short)
    waiter = asyncio.create_task(DockerVerificationRun(metadata, None).execute_prepared(key))
    try:
        await entered.wait()
        await asyncio.sleep(0)
        if cancel:
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        else:
            done, _ = await asyncio.wait({waiter}, timeout=.1)
            assert done, "claim await must have an explicit budget"
            result = waiter.result()
            assert result["outcome"] == "UNKNOWN" and not result["executed"]
        assert native["manual_intervention_required"]
        assert native["execution_id"]
        assert any(not t.done() for t in native["pending_operations"])
        broker._verification_step.assert_not_called()
    finally:
        release.set()
        await asyncio.gather(io, waiter, *tuple(native.get("pending_operations", ())),
                             return_exceptions=True)
    broker._verification_step.assert_not_called()


@pytest.mark.parametrize("outcome,executed,passed", [
    ("unverified", True, False), ("passed", True, True),
    ("check_failed", True, False), ("not_executed", False, False),
    ("policy_denied", False, False), ("UNKNOWN", False, False),
    ("missing_results", False, False),
])
def test_report_keeps_outcomes_separate(outcome, executed, passed):
    report = build_verification_report([Check("check", passed, executed=executed)],
        {"status": "passed", "results": [{"name": "check", "outcome": outcome}]})
    assert report.checks[0].outcome == outcome
    assert report.verified is (outcome == "passed")


async def test_approved_proposal_with_unverified_check_never_reaches_apply(monkeypatch):
    from Sprout.execution.models import ChangeProposalStatus
    from Sprout.runtime.changes import ChangeProposalService
    from Sprout.storage.local.memory import MemoryOperationalStore
    from Sprout.tests.test_r04_f3_verification_gate import fixture

    proposal = fixture()
    checks = tuple(replace(c, executed=True, passed=True) for c in proposal.test_results)
    records = [{"name": c.name, "outcome": "unverified" if i == 0 else "passed"}
               for i, c in enumerate(checks)]
    proposal = replace(proposal, status=ChangeProposalStatus.APPROVED, test_results=checks,
        metadata={"verification": {**proposal.metadata["verification"], "status": "passed",
                                    "results": records}})
    assert not proposal_verification_report(proposal).verified
    monkeypatch.setattr("Sprout.runtime.changes.verification_evidence_applicability",
                        lambda *a, **k: "local_match")
    metadata = SimpleNamespace(get_change_proposal=AsyncMock(return_value=proposal),
        get_task=AsyncMock(), get_workspace=AsyncMock(), save_change_proposal=AsyncMock())
    broker = SimpleNamespace(apply=AsyncMock())
    service = ChangeProposalService(metadata, MemoryOperationalStore(), broker)
    result = await service.apply(proposal.id)
    assert not result.applied
    broker.apply.assert_not_called()


@pytest.mark.parametrize("kind", ["metadata_query", "metadata_write", "cli_communication"])
async def test_slow_underlying_operation_remains_owned_after_budget(kind):
    release, entered = asyncio.Event(), asyncio.Event()
    native = {"cleanup_deadline": time.monotonic() + .02}
    async def operation():
        entered.set()
        await release.wait()
        return kind
    with pytest.raises(TimeoutError):
        await ProcessBroker._verification_wait(native, operation(), 1)
    await entered.wait()
    assert native["manual_intervention_required"]
    pending = set(native["pending_operations"])
    assert len(pending) == 1 and not next(iter(pending)).cancelled()
    release.set()
    assert await asyncio.gather(*pending) == [kind]


async def test_late_cli_spawn_is_reaped_without_starting_a_second_effect():
    release = asyncio.Event()
    native = {"cleanup_deadline": time.monotonic() + .02}
    process = SimpleNamespace(returncode=None, wait=AsyncMock(return_value=0))
    def kill():
        process.returncode = -9
    process.kill = kill
    async def spawn():
        await release.wait()
        return process
    with pytest.raises(TimeoutError):
        await ProcessBroker._verification_wait(native, spawn(), 1, spawn=True)
    tasks = set(native["pending_operations"])
    release.set()
    await asyncio.gather(*tasks)
    await asyncio.sleep(0)
    await asyncio.gather(*tuple(native["pending_operations"]))
    assert process.returncode == -9
    process.wait.assert_awaited_once()
    assert not native["cli_processes"]


async def test_repeated_cancellation_does_not_cancel_underlying_io():
    release, entered = asyncio.Event(), asyncio.Event()
    native = {"cleanup_deadline": time.monotonic() + 1}
    async def operation():
        entered.set()
        await release.wait()
    waiter = asyncio.create_task(ProcessBroker._verification_wait(native, operation(), 1))
    await entered.wait()
    waiter.cancel()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    tasks = set(native["pending_operations"])
    assert len(tasks) == 1 and not next(iter(tasks)).cancelled()
    release.set()
    await asyncio.gather(*tasks)


async def test_cancel_after_spawn_completion_still_reaps_exact_cli():
    native = {}
    process = SimpleNamespace(returncode=None, wait=AsyncMock(return_value=0))
    def kill():
        process.returncode = -9
    process.kill = kill
    async def spawn():
        # The broker's completion callback runs first, cancellation second.
        asyncio.current_task().add_done_callback(lambda _: waiter.cancel())
        return process
    waiter = asyncio.create_task(ProcessBroker._verification_wait(native, spawn(), 1, spawn=True))
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await asyncio.gather(*tuple(native["pending_operations"]))
    await asyncio.sleep(0)  # deliver the wait task's registered completion callback
    assert process.returncode == -9
    process.wait.assert_awaited_once()
    assert not native["cli_processes"]


async def test_no_new_operation_after_cleanup_deadline():
    native = {"cleanup_deadline": time.monotonic() - 1}
    operation = AsyncMock()
    with pytest.raises(TimeoutError):
        await ProcessBroker._verification_wait(native, operation(), 1)
    operation.assert_not_awaited()


async def test_stopping_runtime_rejects_new_verification_before_any_capture(f3, monkeypatch):
    runtime = f3.rig.runtime
    runtime._verification_stopping = True
    subject = AsyncMock(side_effect=AssertionError("No new subject/capture"))
    monkeypatch.setattr(runtime, "_project_analysis_subject", subject)
    with pytest.raises(PermissionError, match="runtime_stopping"):
        await runtime._evaluate_host_verification(f3.rig.node)
    with pytest.raises(PermissionError, match="runtime_stopping"):
        await runtime._capture_verification_view(None, ["src/app.py"])
    with pytest.raises(PermissionError, match="runtime_stopping"):
        await runtime.bind_task_verification("task", None, paths=(), export_parent=f3.root,
            images={}, source_digests={})
    subject.assert_not_awaited()
    f3.spawn.assert_not_called()


async def test_stop_retains_storage_and_custody_while_io_is_pending(f3):
    runtime = f3.rig.runtime
    release = asyncio.Event()
    io = asyncio.create_task(release.wait())
    key = VerificationManifestV1("{}")
    native = {"pending_operations": {io}, "container_id": "1" * 64,
              "manual_intervention_required": True}
    runtime._verification_preparations[key] = {"owner": asyncio.current_task(), "native": native}
    runtime._release_verification_preparation(key)
    assert key in runtime._verification_preparations
    # An exhausted stop is covered by the original unchanged RED; here the I/O
    # returns while shutdown waits, but explicit manual uncertainty remains.
    release.set()
    await io
    await runtime.stop()
    assert key in runtime._verification_preparations
    assert runtime._verification_shutdown["state"] == "UNKNOWN"
    assert runtime._verification_shutdown["storage_retained"]
    assert await runtime.storage.metadata.get_task(f3.rig.task.id) is not None
    with pytest.raises(RuntimeError, match="manual_intervention"):
        await runtime.start()
    # Test-only resolution of a synthetic record, no container or authority.
    native["manual_intervention_required"] = False
    runtime._release_verification_preparation(key)
    assert key not in runtime._verification_preparations
