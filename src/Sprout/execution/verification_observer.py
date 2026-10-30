"""Container-side structured observer. No Sprout authority or host credentials.

This is a separate parent of target code, not an OS isolation implementation.
Native confinement and tool commissioning must still be tested independently.
"""

import base64
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def materialize_inputs(selected, source=Path("/inputs"), destination=Path("/workspace")):
    """Copy exact pinned inputs into the disposable container tmpfs, never a host mount."""
    if type(selected) is not dict or not selected or len(selected) > 64:
        raise ValueError("observer_copy_inventory")
    total = 0
    for name, expected in selected.items():
        relative = Path(name)
        if (not isinstance(name, str) or relative.is_absolute() or ".." in relative.parts
                or relative.as_posix() != name or relative.suffix != ".py"
                or any(p.startswith(".") for p in relative.parts)):
            raise ValueError("observer_copy_path")
        target = source / relative
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError("observer_copy_link")
        with target.open("rb") as stream:
            content = stream.read(100001)
        total += len(content)
        if (len(content) > 100000 or total > 1000000
                or hashlib.sha256(content).hexdigest() != expected):
            raise ValueError("observer_copy_content")
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            stream.write(content)


def observe(request):
    import resource

    sys.dont_write_bytecode = True
    if request["cwd"] != "/workspace" or Path.cwd() != Path("/workspace"):
        raise ValueError("observer_cwd_mismatch")
    if request.get("materialize") is not None:
        materialize_inputs(request["materialize"])
    command = request["argv"]
    if (
        not command
        or command[0] not in {"python", "pytest", "ruff"}
        or any(type(x) is not str or "\0" in x for x in command)
    ):
        raise ValueError("observer_command_mismatch")
    identities = {}
    for name, identity in request["tools"].items():
        path = Path(identity["path"])
        if (
            not path.is_absolute()
            or not path.is_relative_to("/usr")
            or path.is_symlink()
            or sha(path) != identity["sha256"]
        ):
            raise ValueError("observer_tool_identity_mismatch")
        identities[name] = identity
    executable = identities[command[0]]["path"]
    argv = [executable, *command[1:]]
    pytest = command[0] == "pytest" or command[:3] == ["python", "-m", "pytest"]
    if pytest:
        if sha("/pytest_observer.py") != request["pytest_observer_sha"]:
            raise ValueError("observer_pytest_probe_mismatch")
        arguments = command[1:] if command[0] == "pytest" else command[3:]
        argv = [
            identities["python"]["path"],
            "-I",
            "/pytest_observer.py",
            "-",
            request["nonce"],
            *arguments,
        ]
    limit = request["max_output"]
    if type(limit) is not int or not 1024 <= limit <= 65536:
        raise ValueError("observer_output_budget")
    # The Linux image is a prerequisite. File output is bounded even if target
    # children keep writing; this does not substitute for container memory/PID limits.
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "RUFF_NO_CACHE": "1",
    }
    started = time.monotonic()
    status = "completed"
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        child = subprocess.Popen(
            argv,
            cwd="/workspace",
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            code = child.wait(timeout=request["max_seconds"])
        except subprocess.TimeoutExpired:
            status = "timeout"
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
            code = 124
        stdout.seek(0)
        stderr.seek(0)
        out, err = stdout.read(limit + 1), stderr.read(limit + 1)
    if len(out) + len(err) > limit:
        status = "output_limit"
        code = 125
    code = code if 0 <= code <= 255 else 128 + min(abs(code), 127)
    return {
        "schema_version": 1,
        "context": request["context"],
        "nonce": request["nonce"],
        "argv": command,
        "cwd": request["cwd"],
        "observer_sha": sha(__file__),
        "tools": identities,
        "exit_code": code,
        "status": status,
        "duration_ms": (time.monotonic() - started) * 1000,
        "stdout": out[:limit].decode("utf-8", errors="replace"),
        "stderr": err[: max(0, limit - len(out))].decode("utf-8", errors="replace"),
    }


def main():
    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    result = observe(request)
    print("SPROUT_COMMAND_REPORT " + request["nonce"] + " " + json.dumps(result))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
