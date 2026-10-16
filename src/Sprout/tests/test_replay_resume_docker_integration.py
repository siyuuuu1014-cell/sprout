"""Opt-in real Docker recovery; controlled code completion, no production model."""

import json
import os
import subprocess

import pytest

from Sprout.evolution.replay_resume import resume_replay
from Sprout.runtime.factory import create_runtime
from Sprout.runtime.runtime import Runtime
from Sprout.security.access import AccessDecision, PolicyDecision
from Sprout.storage.local.sqlite.metadata import open_metadata_store
from Sprout.task.models import TaskResult, TaskStatus
from Sprout.tests.test_replay_resume import fixture

IMAGE = os.getenv("SPROUT_VERIFICATION_TEST_IMAGE", "")
pytestmark = pytest.mark.skipif(not IMAGE, reason="explicit pinned verification image required")


class FixtureDockerReviewPolicy:
    def decide(self, request):
        argv = tuple(request.arguments.get("command", ()))
        if argv[:3] == ("docker", "image", "inspect") or argv[:2] == ("docker", "run"):
            return PolicyDecision(AccessDecision.REQUIRE_APPROVAL, "explicit fixture Docker review")
        return PolicyDecision(AccessDecision.DENY, "fixture permits no other process")


def clean_owned_containers(records, directory):
    """Evidence retained; only exact recorded, labelled, naturally stopped fixtures removed."""
    # Stored names are obtained asynchronously by the caller, not enumerated globally.
    for name in records:
        inspection = subprocess.run(["docker", "inspect", name], capture_output=True,
                                    text=True, timeout=15)
        if inspection.returncode:
            continue
        state = json.loads(inspection.stdout)[0]
        if state["Config"]["Labels"].get("sprout.role") != "independent-verification":
            raise AssertionError("Unexpected container ownership")
        if state["State"]["Running"]:
            subprocess.run(["docker", "wait", name], check=True, capture_output=True, timeout=20)
            inspection = subprocess.run(["docker", "inspect", name], check=True,
                                        capture_output=True, text=True, timeout=15)
            state = json.loads(inspection.stdout)[0]
        (directory / (name + ".inspection.json")).write_text(
            json.dumps(state, indent=2), encoding="utf-8")
        assert not state["State"]["Running"]
        subprocess.run(["docker", "rm", name], check=True, capture_output=True, timeout=15)


@pytest.mark.parametrize("timeout", [False, True], ids=["approved-observation", "client-timeout"])
async def test_real_docker_approval_recovery_and_no_reexecution(tmp_path, monkeypatch, timeout):
    database, task, settings, principal = await fixture(tmp_path, image=IMAGE)
    if timeout:
        # Change the frozen test before rebuilding the fixture plan; never relax an existing oracle.
        from dataclasses import replace

        from Sprout.evolution.replay_oracle import digest, snapshot

        repo = database.parent / "repo"
        (repo / "test_app.py").write_text(
            "import time\ndef test_value(): time.sleep(4); assert 1 == 1\n", encoding="utf-8")
        from Sprout.evolution.isolated_replay import git

        git(repo, "add", "test_app.py")
        git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid.example",
            "-c", "commit.gpgsign=false", "commit", "-qm", "slow fixture")
        store = open_metadata_store(str(database))
        try:
            saved = await store.get_task(task.id)
            payload = {**saved.metadata["replay_plan"]["payload"],
                       "revision": git(repo, "rev-parse", "HEAD"),
                       "oracle_snapshot": snapshot(repo, ("test_app.py",))}
            # Test fixture setup only, before any approval/claim/execution.
            plan = {"payload": payload, "fingerprint": digest(payload)}
            await store.save_task(replace(saved, metadata={**saved.metadata, "replay_plan": plan}))
        finally:
            store.close()

    executions = []

    async def complete_fixture(runtime, fresh, *, artifact_snapshot):
        executions.append(fresh.id)
        await runtime.storage.metadata.update_task_status(fresh.id, TaskStatus.COMPLETED)
        return TaskResult(task_id=fresh.id, status=TaskStatus.COMPLETED)

    monkeypatch.setattr(Runtime, "_execute_task", complete_fixture)
    original_process = Runtime.run_task_process
    process_calls = []

    async def controlled_process(runtime, task_id, command, *, timeout_seconds=None):
        process_calls.append(tuple(command))
        duration = 1 if timeout and command[:2] == ("docker", "run") else timeout_seconds
        return await original_process(runtime, task_id, command, timeout_seconds=duration)

    monkeypatch.setattr(Runtime, "run_task_process", controlled_process)

    def factory(config):
        runtime = create_runtime(config)
        runtime._process_broker._policy = FixtureDockerReviewPolicy()
        return runtime

    names = []
    store = None
    try:
        parked = await resume_replay(database, task.id, settings=settings, principal=principal,
                                     runtime_factory=factory)
        assert parked["approval_id"] and not executions
        result = await resume_replay(database, task.id, settings=settings, principal=principal,
                                     approval_id=parked["approval_id"], runtime_factory=factory)
        assert executions == [task.id]
        assert not result["validation_committed"]
        report = json.loads((database.parent / "independent-measurement.json").read_text())
        store = open_metadata_store(str(database))
        for record in report["records"]:
            saved = await store.get_verification_execution(record["execution_id"])
            names.append(saved["container_name"])
            assert saved["state"] == ("uncertain" if timeout else "completed")
        if timeout:
            assert result["measurement_status"] == "waiting_resource"
            assert len(names) == 1
            inspection = subprocess.run(["docker", "inspect", names[0]], check=True,
                                        capture_output=True, text=True, timeout=15)
            assert json.loads(inspection.stdout)[0]["State"]["Running"]
        else:
            assert result["measurement_status"] == "observed" and len(names) == 2
            assert all(record["outcome"] == "passed" for record in report["records"])
        call_count = len(process_calls)
        again = await resume_replay(database, task.id, settings=settings, principal=principal,
                                    runtime_factory=factory)
        assert again["status"] == "waiting_resource"
        assert len(process_calls) == call_count and executions == [task.id]
    finally:
        try:
            clean_owned_containers(names, database.parent)
        finally:
            if store is not None:
                store.close()


async def test_unavailable_docker_endpoint_waits_before_claim_and_recovers(tmp_path, monkeypatch):
    database, task, settings, principal = await fixture(tmp_path, image=IMAGE)
    executions = []

    async def complete_fixture(runtime, fresh, *, artifact_snapshot):
        executions.append(fresh.id)
        await runtime.storage.metadata.update_task_status(fresh.id, TaskStatus.COMPLETED)
        return TaskResult(task_id=fresh.id, status=TaskStatus.COMPLETED)

    monkeypatch.setattr(Runtime, "_execute_task", complete_fixture)

    def factory(config):
        runtime = create_runtime(config)
        runtime._process_broker._policy = FixtureDockerReviewPolicy()
        # Test-only forwarding to a refused local endpoint, never stop the daemon.
        runtime._process_broker._extra_env_names = ("DOCKER_HOST",)
        return runtime

    previous_host = os.environ.get("DOCKER_HOST")
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:1")
    first = await resume_replay(database, task.id, settings=settings, principal=principal,
                                runtime_factory=factory)
    failed = await resume_replay(database, task.id, settings=settings, principal=principal,
                                 approval_id=first["approval_id"], runtime_factory=factory)
    assert failed["reason"] == "image_unavailable_unapproved_or_incompatible"
    assert not executions
    store = open_metadata_store(str(database))
    try:
        assert await store.get_replay_resume(task.id) is None
    finally:
        store.close()
    if previous_host is None:
        monkeypatch.delenv("DOCKER_HOST")
    else:
        monkeypatch.setenv("DOCKER_HOST", previous_host)
    names = []
    try:
        recovered = await resume_replay(database, task.id, settings=settings, principal=principal,
                                        runtime_factory=factory)
        assert recovered["measurement_status"] == "observed"
        assert executions == [task.id]
        report = json.loads((database.parent / "independent-measurement.json").read_text())
        names = [record["container_name"] for record in report["records"]]
        assert len(names) == 2
    finally:
        clean_owned_containers(names, database.parent)
