from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.docker_verification import DockerVerificationBackend, validate_image
from Sprout.execution.models import ProcessResult
from Sprout.execution.verification_backend import VerificationRequest


@pytest.fixture
def request_fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    inputs = tmp_path / "sanitized-export"
    inputs.mkdir()
    (inputs / "app.py").write_text("value = 1", encoding="utf-8")
    observer = tmp_path / "observer.py"
    observer.write_text("# controlled observer", encoding="utf-8")
    return VerificationRequest(
        "fresh-task",
        source,
        inputs,
        observer,
        "sha256:" + "a" * 64,
        ("/tmp/report.json", "nonce", "-q", "tests"),
    )


def test_plan_has_no_network_privileges_pulls_or_writable_host_mounts(request_fixture):
    command = DockerVerificationBackend().plan(request_fixture)
    for option in (
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=64",
        "--memory=512m",
        "--user=65532:65532",
        "--restart=no",
    ):
        assert option in command
    mounts = [command[index + 1] for index, part in enumerate(command) if part == "--mount"]
    assert len(mounts) == 2 and all(mount.endswith(",readonly") for mount in mounts)
    assert all(str(request_fixture.source_root) not in mount for mount in mounts)
    assert "--privileged" not in command and "--env-file" not in command
    assert not any("docker.sock" in part for part in command)
    assert request_fixture.image in command


@pytest.mark.parametrize(
    "image", ["python:latest", "python:3.13", "", "sha256:123", "--privileged"]
)
def test_mutable_or_invalid_images_are_rejected(image):
    with pytest.raises(ValueError):
        validate_image(image)


@pytest.mark.parametrize(
    "name", [".env", "private.key", "credentials.json", ".git/config", "data/local.db"]
)
def test_private_input_is_rejected(request_fixture, name):
    target = request_fixture.input_root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("private", encoding="utf-8")
    with pytest.raises(ValueError):
        DockerVerificationBackend().plan(request_fixture)


def test_original_workspace_cannot_be_mounted(request_fixture):
    with pytest.raises(ValueError, match="overlap"):
        DockerVerificationBackend().plan(
            replace(request_fixture, input_root=request_fixture.source_root)
        )


def test_observer_cannot_be_candidate_owned(request_fixture):
    observer = request_fixture.input_root / "observer.py"
    observer.write_text("# untrusted", encoding="utf-8")
    with pytest.raises(ValueError, match="independent"):
        DockerVerificationBackend().plan(replace(request_fixture, observer=observer))


async def test_missing_image_or_approval_never_triggers_pull_or_host_execution(request_fixture):
    runtime = AsyncMock()
    runtime.run_task_process.return_value = ProcessResult(("docker",), allowed=False)
    outcome = await DockerVerificationBackend().inspect_image(
        runtime, request_fixture.task_id, request_fixture.image
    )
    assert not outcome.allowed
    runtime.run_task_process.assert_awaited_once_with(
        "fresh-task", ("docker", "image", "inspect", request_fixture.image), timeout_seconds=15
    )


@pytest.mark.parametrize("image", [None, "sha256:" + "a" * 64])
async def test_code_replay_waits_without_commissioned_backend(tmp_path, monkeypatch, image):
    from types import SimpleNamespace

    from Sprout.evolution.replay_runner import ReplayRunner
    from Sprout.gateway.local_identity import current_cli_principal

    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    runtime = AsyncMock()
    runtime.get_task.return_value = SimpleNamespace(workspace_id="workspace", execution_mode="code")
    result = await ReplayRunner(runtime).run(
        "source-task",
        cli_principal=current_cli_principal(),
        revision="fixed-commit",
        verification_commands=("echo success",) if image else ("pytest",),
        regression_commands=("pytest regression",),
        oracle_paths=("tests",),
        verification_image=image,
        settings=object(),
        output_dir=tmp_path,
    )
    assert result.status == "waiting_resource"
    assert not result.task_id
    runtime.execute.assert_not_awaited()
    runtime.run_task_process.assert_not_awaited()
