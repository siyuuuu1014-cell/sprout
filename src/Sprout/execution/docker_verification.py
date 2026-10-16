"""Fail-closed Docker verification plans, separate from agent execution."""

import json
import re
from pathlib import Path
from uuid import uuid4

from Sprout.execution.verification_backend import AuthorizedProcessRunner, VerificationRequest
from Sprout.workspace.classifier import classify, is_device_namespace
from Sprout.workspace.models import ResourceKind


def validate_image(image: str) -> None:
    if not re.fullmatch(r"(?:[a-zA-Z0-9][a-zA-Z0-9._/:-]*@)?sha256:[0-9a-f]{64}", image):
        raise ValueError("A pinned local image ID or repository digest is required")


def image_ready(result) -> bool:
    if not result.allowed or result.exit_code != 0:
        return False
    try:
        image = json.loads(result.stdout)[0]
        labels = image.get("Config", {}).get("Labels") or {}
        return (
            image.get("Os") == "linux"
            and labels.get("sprout.role") == "independent-verification"
            and labels.get("sprout.verification.schema") == "1"
        )
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return False


def safe_path(path: Path) -> Path:
    if is_device_namespace(path) or str(path).startswith("\\\\"):
        raise ValueError("Device and network mount paths are not allowed")
    if path.is_symlink():
        raise ValueError("Symbolic link mount roots are not allowed")
    resolved = path.resolve(strict=True)
    if any(character in str(resolved) for character in (",", "\n", "\r")):
        raise ValueError("Mount path contains Docker option delimiters")
    return resolved


class DockerVerificationBackend:
    """Docker launch plan; durable ownership is provided by DockerVerificationRun.

    The input must be a dedicated sanitized export, not a Git clone or original
    workspace. No mutable image tags, host-writable mounts or automatic pull.
    """

    def plan(self, request: VerificationRequest) -> tuple[str, ...]:
        validate_image(request.image)
        root = safe_path(request.input_root)
        source = safe_path(request.source_root)
        observer = safe_path(request.observer)
        if root.is_relative_to(source) or source.is_relative_to(root):
            raise ValueError("Verification input must not overlap the original workspace")
        if not root.is_dir() or not observer.is_file() or observer.is_relative_to(root):
            raise ValueError("Observer must be an independent file outside verification input")
        for entry in root.rglob("*"):
            if entry.is_symlink() or not entry.resolve().is_relative_to(root):
                raise ValueError("Verification input contains a link or external resource")
            relative = entry.relative_to(root)
            if any(
                part.casefold() in {".git", ".venv", ".ssh", ".sprout", ".workbuddy"}
                for part in relative.parts
            ):
                raise ValueError("Verification input contains private/runtime directories")
            if entry.is_file() and classify(relative, workspace_root=root) in {
                ResourceKind.SECRET,
                ResourceKind.SENSITIVE,
                ResourceKind.EXTERNAL,
                ResourceKind.DATA,
            }:
                raise ValueError("Verification input contains a private or data resource")
        if not request.arguments:
            raise ValueError("Explicit pytest arguments are required")
        name = "sprout-verification-" + uuid4().hex
        return (
            "docker",
            "run",
            "--pull=never",
            "--name",
            name,
            "--label",
            "sprout.role=independent-verification",
            "--network=none",
            "--read-only",
            "--restart=no",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65532:65532",
            "--cpus=1",
            "--memory=512m",
            "--memory-swap=512m",
            "--pids-limit=64",
            "--log-opt=max-size=1m",
            "--log-opt=max-file=1",
            "--no-healthcheck",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
            "--mount",
            f"type=bind,src={root},dst=/workspace,readonly",
            "--mount",
            f"type=bind,src={observer},dst=/observer.py,readonly",
            "--workdir=/workspace",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            "--entrypoint=python",
            request.image,
            "-I",
            "/observer.py",
            *request.arguments,
        )

    async def inspect_image(self, runtime: AuthorizedProcessRunner, task_id: str, image: str):
        validate_image(image)
        return await runtime.run_task_process(
            task_id, ("docker", "image", "inspect", image), timeout_seconds=15
        )
