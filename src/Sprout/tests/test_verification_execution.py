import asyncio
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from Sprout.evolution.pytest_measurement import summarize
from Sprout.execution.models import ProcessResult
from Sprout.execution.verification_backend import VerificationRequest
from Sprout.execution.verification_export import export_input
from Sprout.execution.verification_run import DockerVerificationRun
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import Task, TaskStatus


def test_export_excludes_git_caches_and_preserves_original(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    repo = tmp_path / "clone"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git/config").write_text("origin", encoding="utf-8")
    (repo / "app.py").write_text("value = 1", encoding="utf-8")
    output = tmp_path / "export"
    assert set(export_input(repo, output, original)) == {"app.py"}
    assert not (output / ".git").exists()
    assert (repo / ".git/config").exists()


def test_export_rejects_sensitive_files(tmp_path):
    original, repo = tmp_path / "original", tmp_path / "clone"
    original.mkdir()
    repo.mkdir()
    (repo / ".env").write_text("private", encoding="utf-8")
    with pytest.raises(ValueError, match="Private"):
        export_input(repo, tmp_path / "export", original)


def test_export_honors_custom_classification_without_relaxing_secret_floor(tmp_path):
    original, repo = tmp_path / "original", tmp_path / "clone"
    original.mkdir()
    repo.mkdir()
    (repo / "private.custom").write_text("private", encoding="utf-8")
    with pytest.raises(ValueError, match="Private"):
        export_input(repo, tmp_path / "export", original, classify_overrides={"*.custom": "secret"})


async def prepare_run(tmp_path):
    original, exported = tmp_path / "original", tmp_path / "export"
    original.mkdir()
    exported.mkdir()
    (exported / "app.py").write_text("value = 1", encoding="utf-8")
    observer = tmp_path / "observer.py"
    observer.write_text("# observer", encoding="utf-8")
    store = open_metadata_store(str(tmp_path / "metadata.db"))
    task = Task(status=TaskStatus.COMPLETED, metadata={"execution_mode": "code"})
    await store.save_task(task)
    request = VerificationRequest(
        task.id, original, exported, observer, "sha256:" + "a" * 64, ("-", "nonce", "tests")
    )
    runtime = AsyncMock()
    inspection = ProcessResult(
        ("docker",),
        exit_code=0,
        stdout=json.dumps(
            [
                {
                    "Os": "linux",
                    "Config": {
                        "Labels": {
                            "sprout.role": "independent-verification",
                            "sprout.verification.schema": "1",
                        }
                    },
                }
            ]
        ),
    )
    payload = {
        "schema_version": 1,
        "nonce": "nonce",
        "exit_code": 0,
        "collected": ["tests/test_ok.py::test_ok"],
        "collection_errors": [],
        "runner": {"probe_hash": hashlib.sha256(observer.read_bytes()).hexdigest()},
        "reports": [
            {"nodeid": "tests/test_ok.py::test_ok", "phase": phase, "outcome": "passed"}
            for phase in ("setup", "call", "teardown")
        ],
    }
    completed = ProcessResult(
        ("docker",), exit_code=0, stdout="SPROUT_PYTEST_REPORT nonce " + json.dumps(payload)
    )
    return store, request, runtime, inspection, completed


async def test_completed_result_is_cached_across_restart(tmp_path):
    store, request, runtime, inspection, completed = await prepare_run(tmp_path)
    runtime.run_task_process.side_effect = [inspection, completed]
    result = await DockerVerificationRun(store, summarize).execute(runtime, request, slot="test-0")
    assert not result["waiting"] and result["input_unchanged"]
    assert runtime.run_task_process.call_count == 2
    store.close()
    reopened = open_metadata_store(str(tmp_path / "metadata.db"))
    try:
        cached = await DockerVerificationRun(reopened, summarize).execute(
            runtime, request, slot="test-0"
        )
        assert cached == result
        assert runtime.run_task_process.call_count == 2
    finally:
        reopened.close()


@pytest.mark.parametrize("problem", ["timeout", "missing", "cancel"])
async def test_unknown_results_never_reexecute(tmp_path, problem):
    store, request, runtime, inspection, completed = await prepare_run(tmp_path)
    outcome = (
        asyncio.CancelledError()
        if problem == "cancel"
        else ProcessResult(("docker",), error="Process timed out")
        if problem == "timeout"
        else ProcessResult(("docker",), exit_code=0)
    )
    runtime.run_task_process.side_effect = [inspection, outcome]
    run = DockerVerificationRun(store, summarize)
    try:
        if problem == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await run.execute(runtime, request, slot="test-0")
        else:
            assert (await run.execute(runtime, request, slot="test-0"))["waiting"]
        assert (await run.execute(runtime, request, slot="test-0"))["waiting"]
        assert runtime.run_task_process.call_count == 2
    finally:
        store.close()


async def test_claim_is_owner_bound_and_single_winner(tmp_path):
    store, request, runtime, inspection, completed = await prepare_run(tmp_path)
    other = open_metadata_store(str(tmp_path / "metadata.db"))
    record = {"id": "execution", "task_id": request.task_id, "owner": "first", "signature": "hash"}
    try:
        assert await store.claim_verification_execution(record)
        assert not await other.claim_verification_execution({**record, "owner": "second"})
        assert not await other.finish_verification_execution("execution", "second", "completed", {})
        assert await store.finish_verification_execution("execution", "first", "uncertain", {})
        assert not await other.claim_verification_execution(record)
    finally:
        other.close()
        store.close()


async def test_failed_result_commit_cannot_be_reexecuted(tmp_path):
    store, request, runtime, inspection, completed = await prepare_run(tmp_path)
    runtime.run_task_process.side_effect = [inspection, completed]
    original_finish = store.finish_verification_execution
    store.finish_verification_execution = AsyncMock(return_value=False)
    try:
        run = DockerVerificationRun(store, summarize)
        assert (await run.execute(runtime, request, slot="test-0"))["waiting"]
        store.finish_verification_execution = original_finish
        assert (await run.execute(runtime, request, slot="test-0"))["waiting"]
        assert runtime.run_task_process.call_count == 2
    finally:
        store.close()


async def test_two_connections_cannot_run_same_slot_twice(tmp_path):
    store, request, runtime, inspection, completed = await prepare_run(tmp_path)
    other = open_metadata_store(str(tmp_path / "metadata.db"))

    async def process(task_id, command, **kwargs):
        return inspection if command[1:3] == ("image", "inspect") else completed

    runtime.run_task_process.side_effect = process
    try:
        await asyncio.gather(
            DockerVerificationRun(store, summarize).execute(runtime, request, slot="test-0"),
            DockerVerificationRun(other, summarize).execute(runtime, request, slot="test-0"),
        )
        actual_runs = [
            call for call in runtime.run_task_process.call_args_list if call.args[1][1] == "run"
        ]
        assert len(actual_runs) == 1
    finally:
        other.close()
        store.close()


async def test_unconfirmed_or_readonly_tasks_cannot_claim_verification(tmp_path):
    store = open_metadata_store(str(tmp_path / "metadata.db"))
    try:
        for mode in ("unconfirmed", "read_only"):
            task = Task(status=TaskStatus.COMPLETED, metadata={"execution_mode": mode})
            await store.save_task(task)
            assert not await store.claim_verification_execution(
                {"id": mode, "task_id": task.id, "owner": "owner", "signature": "hash"}
            )
    finally:
        store.close()
