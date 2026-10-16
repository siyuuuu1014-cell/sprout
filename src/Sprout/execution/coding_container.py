"""Offline coding executor over a dedicated export, never the evidence directory.

The host retains model credentials, approval authority and immutable oracles.
This adapter is not yet a Runtime tool-routing replacement.
"""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from Sprout.execution import coding_worker
from Sprout.execution.docker_verification import DockerVerificationBackend, safe_path
from Sprout.execution.verification_backend import VerificationRequest
from Sprout.execution.verification_export import private_resource


@dataclass(frozen=True, slots=True)
class CodingCommand:
    task_id: str
    original: Path
    evidence: Path
    exported: Path
    image: str
    command: tuple[str, ...]
    oracle_paths: tuple[str, ...]
    relative_cwd: str = "."
    read_only_input: bool = False


def coding_node_directory_name(node_id):
    if not isinstance(node_id, str):
        raise ValueError("Confined node directory identity required")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", node_id):
        return "coding-node-" + node_id
    if len(node_id) <= 128 and re.fullmatch(r"[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+){1,4}", node_id):
        return "coding-node-sha256-" + hashlib.sha256(node_id.encode()).hexdigest()
    raise ValueError("Confined node directory identity required")


def container_cwd(exported: Path, relative: str) -> str:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
        raise ValueError("Confined portable coding cwd required")
    root = safe_path(exported)
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Linked coding cwd forbidden")
    if not current.is_dir() or not current.resolve().is_relative_to(root):
        raise ValueError("Existing coding cwd inside export required")
    return "/workspace" + ("/" + path.as_posix() if path.parts else "")


def coding_plan(request: CodingCommand) -> tuple[str, ...]:
    evidence = safe_path(request.evidence)
    exported = safe_path(request.exported)
    # Only a specifically named child export may be writable. Parent directories,
    # metadata databases, Git checkout and frozen files are never mount targets.
    if exported != evidence / "coding-input" or not exported.is_dir():
        raise ValueError("Dedicated coding-input export required")
    if not request.oracle_paths:
        raise ValueError("Explicit frozen oracle exclusions required")
    for name in request.oracle_paths:
        path = Path(name)
        if (path.is_absolute() or ".." in path.parts or not path.parts
                or (exported / path).exists() or (exported / path).is_symlink()):
            raise ValueError("Coding input must exclude frozen oracles")
    for entry in exported.rglob("*"):
        if entry.is_file() and private_resource(entry.relative_to(exported), exported):
            raise ValueError("Private coding input unsupported")
    if (not request.command or any(not isinstance(value, str) or "\x00" in value
                                   for value in request.command)):
        raise ValueError("Structured coding command required")
    encoded = base64.b64encode(json.dumps(request.command).encode()).decode()
    if len(encoded) > 64 * 1024:
        raise ValueError("Coding command too large")
    command = list(DockerVerificationBackend().plan(VerificationRequest(
        request.task_id, request.original, exported, Path(coding_worker.__file__).resolve(),
        request.image, (encoded,))))
    mount = f"type=bind,src={exported},dst=/workspace,readonly"
    if not request.read_only_input:
        command[command.index(mount)] = f"type=bind,src={exported},dst=/workspace"
    command[command.index("sprout.role=independent-verification")] = "sprout.role=isolated-coding"
    name_index = command.index("--name") + 1
    command[name_index] = command[name_index].replace("sprout-verification-", "sprout-coding-")
    cwd_index = command.index("--workdir=/workspace")
    command[cwd_index] = "--workdir=" + container_cwd(exported, request.relative_cwd)
    return tuple(command)


def coding_image_ready(result) -> bool:
    if not result.allowed or result.exit_code != 0:
        return False
    try:
        info = json.loads(result.stdout)[0]
        labels = info["Config"].get("Labels") or {}
        return (info.get("Os") == "linux" and labels.get("sprout.role") == "isolated-coding"
                and labels.get("sprout.coding.schema") == "1")
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        return False
