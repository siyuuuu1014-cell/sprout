import base64
import json
from dataclasses import replace

import pytest

from Sprout.execution.coding_container import CodingCommand, coding_image_ready, coding_plan
from Sprout.execution.models import ProcessResult


def fixture(tmp_path):
    original = tmp_path / "original"
    evidence = tmp_path / "evidence"
    exported = evidence / "coding-input"
    original.mkdir()
    exported.mkdir(parents=True)
    (exported / "app.py").write_text("value = 1", encoding="utf-8")
    (evidence / "metadata.db").write_text("host-only", encoding="utf-8")
    return CodingCommand("task", original, evidence, exported, "sha256:" + "a" * 64,
                         ("python", "-c", "print(1)"), ("tests",))


def test_only_dedicated_export_writable_no_host_authorities(tmp_path):
    request = fixture(tmp_path)
    command = coding_plan(request)
    assert "--network=none" in command and "--read-only" in command
    assert "--user=65532:65532" in command and "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command and "--pull=never" in command
    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
    assert len(mounts) == 2
    assert mounts[0] == f"type=bind,src={request.exported},dst=/workspace"
    assert mounts[1].endswith("dst=/observer.py,readonly")
    assert "metadata.db" not in str(command)
    assert json.loads(base64.b64decode(command[-1])) == list(request.command)


@pytest.mark.parametrize("target", ["parent", "original", "other"])
def test_broad_or_original_mount_rejected(tmp_path, target):
    request = fixture(tmp_path)
    path = {"parent": request.evidence, "original": request.original,
            "other": request.exported.parent / "repo"}[target]
    if target == "other":
        path.mkdir()
    with pytest.raises(ValueError, match="Dedicated"):
        coding_plan(replace(request, exported=path))


def test_oracles_and_private_files_rejected(tmp_path):
    request = fixture(tmp_path)
    (request.exported / "tests").mkdir()
    with pytest.raises(ValueError, match="oracles"):
        coding_plan(request)


@pytest.mark.parametrize("role", ["independent-verification", "isolated-coding"])
def test_image_roles_are_not_interchangeable(role):
    result = ProcessResult(("docker",), exit_code=0, stdout=json.dumps([
        {"Os": "linux", "Config": {"Labels": {"sprout.role": role, "sprout.coding.schema": "1"}}}]))
    assert coding_image_ready(result) is (role == "isolated-coding")


def test_nested_workdir_maps_to_container_without_extra_mount(tmp_path):
    request = fixture(tmp_path)
    (request.exported / "src").mkdir()
    command = coding_plan(replace(request, relative_cwd="src"))
    assert "--workdir=/workspace/src" in command
    assert command.count("--mount") == 2


@pytest.mark.parametrize("cwd", ["../", "/tmp", "C:/temp", "src\\outside", "missing"])
def test_cwd_escape_or_missing_rejected(tmp_path, cwd):
    with pytest.raises(ValueError):
        coding_plan(replace(fixture(tmp_path), relative_cwd=cwd))
