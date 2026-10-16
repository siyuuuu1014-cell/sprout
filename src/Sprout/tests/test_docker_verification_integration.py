"""Opt-in controlled Docker boundary probe; never mounts the real project."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from Sprout.evolution import pytest_probe
from Sprout.evolution.pytest_measurement import summarize
from Sprout.execution.docker_verification import DockerVerificationBackend, validate_image
from Sprout.execution.verification_backend import VerificationRequest

IMAGE = os.getenv("SPROUT_VERIFICATION_TEST_IMAGE", "")
pytestmark = pytest.mark.skipif(not IMAGE, reason="explicit pinned verification image required")


def test_real_container_readonly_offline_nonroot_and_structured_report(tmp_path):
    validate_image(IMAGE)
    original = tmp_path / "source"
    original.mkdir()
    (original / "untouched.txt").write_text("original", encoding="utf-8")
    exported = tmp_path / "export"
    (exported / "tests").mkdir(parents=True)
    (exported / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (exported / "tests" / "test_boundary.py").write_text(
        "import os, socket\n"
        "from pathlib import Path\n"
        "import pytest\n"
        "def test_boundary():\n"
        "    assert os.getuid() != 0\n"
        "    assert not Path('/var/run/docker.sock').exists()\n"
        "    with pytest.raises(OSError):\n"
        "        Path('/workspace/injected.py').write_text('changed')\n"
        "    with pytest.raises(OSError):\n"
        "        Path('/observer.py').write_text('changed')\n"
        "    with pytest.raises(OSError):\n"
        "        socket.create_connection(('1.1.1.1', 443), timeout=1)\n"
        "    Path('/tmp/scratch').write_text('allowed temporary write')\n",
        encoding="utf-8",
    )
    request = VerificationRequest(
        "boundary-fixture",
        original,
        exported,
        Path(pytest_probe.__file__).resolve(),
        IMAGE,
        ("-", "boundary-nonce", "-q", "-p", "no:cacheprovider", "tests"),
    )
    command = DockerVerificationBackend().plan(request)
    name = command[command.index("--name") + 1]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (tmp_path / "docker.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (tmp_path / "docker.stderr.txt").write_text(result.stderr, encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        marker = "SPROUT_PYTEST_REPORT boundary-nonce "
        lines = [
            line[len(marker) :] for line in result.stdout.splitlines() if line.startswith(marker)
        ]
        assert len(lines) == 1
        payload = json.loads(lines[0])
        summary = summarize(payload, "boundary-nonce", 0, payload["runner"]["probe_hash"])
        assert summary["observed_all_passed"] and summary["collected_count"] == 1
        assert not summary["trusted_execution"]
        assert (original / "untouched.txt").read_text(encoding="utf-8") == "original"
        assert not (exported / "injected.py").exists()
    finally:
        inspection = subprocess.run(
            ["docker", "inspect", name], capture_output=True, text=True, timeout=15
        )
        if inspection.returncode == 0:
            state = json.loads(inspection.stdout)[0]
            (tmp_path / "container-inspection.json").write_text(
                json.dumps(state, indent=2), encoding="utf-8"
            )
            if (
                state["Config"]["Labels"].get("sprout.role") == "independent-verification"
                and not state["State"]["Running"]
            ):
                subprocess.run(["docker", "rm", name], check=True, capture_output=True, timeout=15)


async def test_real_export_measurement_and_persistent_cache(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from Sprout.evolution.replay_docker import measure_docker
    from Sprout.evolution.replay_oracle import snapshot
    from Sprout.execution.models import ProcessResult
    from Sprout.storage.local.sqlite.metadata import open_metadata_store
    from Sprout.task.models import Task, TaskStatus

    original = tmp_path / "original"
    original.mkdir()
    (original / ".env").write_text("must not be mounted", encoding="utf-8")
    repo = tmp_path / "clone"
    (repo / "tests").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8", newline="\n")
    (repo / "tests/test_ok.py").write_text(
        "def test_ok():\n    assert 2 + 2 == 4\n", encoding="utf-8", newline="\n"
    )
    directory = tmp_path / "evidence"
    directory.mkdir()
    store = open_metadata_store(str(directory / "metadata.db"))
    task = Task(status=TaskStatus.COMPLETED, metadata={"execution_mode": "code"})
    await store.save_task(task)

    class ControlledFixtureRunner:
        storage = SimpleNamespace(metadata=store)
        calls = 0

        async def run_task_process(self, task_id, command, *, timeout_seconds=None):
            assert task_id == task.id and command[0] == "docker"
            assert command[1:3] == ("image", "inspect") or command[1] == "run"
            self.calls += 1
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return ProcessResult(
                command, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr
            )

    runtime = ControlledFixtureRunner()
    kwargs = dict(
        directory=directory,
        repo=repo,
        original=original,
        image=IMAGE,
        oracle_paths=("tests", "pytest.ini"),
        frozen=snapshot(repo, ("tests", "pytest.ini")),
    )
    records = []
    try:
        records = await measure_docker(
            runtime,
            task.id,
            ("pytest -q -p no:cacheprovider tests",),
            ("pytest -q -p no:cacheprovider tests",),
            **kwargs,
        )
        assert len(records) == 2 and all(item["outcome"] == "passed" for item in records)
        assert runtime.calls == 4
        cached = await measure_docker(
            runtime,
            task.id,
            ("pytest -q -p no:cacheprovider tests",),
            ("pytest -q -p no:cacheprovider tests",),
            **kwargs,
        )
        assert cached == records and runtime.calls == 4
        assert not (directory / "verification-input/.git").exists()
        assert not (directory / "verification-input/.env").exists()
        assert (await store.get_task(task.id)).status is TaskStatus.COMPLETED
        (directory / "records.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    finally:
        for record in records:
            stored = await store.get_verification_execution(record["execution_id"])
            name = stored["container_name"]
            inspection = subprocess.run(
                ["docker", "inspect", name], capture_output=True, text=True, timeout=15
            )
            if inspection.returncode == 0:
                state = json.loads(inspection.stdout)[0]
                if (
                    state["Config"]["Labels"].get("sprout.role") == "independent-verification"
                    and not state["State"]["Running"]
                ):
                    subprocess.run(
                        ["docker", "rm", name], check=True, capture_output=True, timeout=15
                    )
        store.close()
