"""Run L0/L1 in a fresh venv and credential-free source snapshot.

No existing .venv, user home, .env, ignored data, or Git configuration is copied.
Reports and scratch resources remain in a new, explicitly named output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def snapshot(destination: Path) -> None:
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    paths.append("src/Sprout/tests/test_ci_baseline.py")
    # Explicit R01 additions also work before a local commit; never copy arbitrary
    # untracked files, ignored credentials, user databases or the original .venv.
    for folder in ("scripts", "src/Sprout/tests/fixtures", ".github/workflows"):
        paths.extend(
            p.relative_to(ROOT).as_posix()
            for p in (ROOT / folder).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )
    manifest = {}
    for name in sorted(set(filter(None, paths))):
        source = ROOT / name
        if not source.is_file():
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
            raise ValueError(f"unsafe source snapshot entry: {name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    (destination.parent / "source-manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    parser.add_argument("--python", default=sys.executable, help="base interpreter for uv")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    checkout = output / "checkout"
    checkout.mkdir()
    snapshot(checkout)
    home = output / "test-home"
    home.mkdir()
    scratch = output / "temp"
    scratch.mkdir()
    git_config = output / "gitconfig"
    git_config.write_text(
        "[user]\nname = Sprout CI Fixture\nemail = fixture@example.invalid\n",
        encoding="utf-8",
    )
    # Whitelist OS execution essentials; never inherit provider keys, service
    # endpoints, SPROUT_CONFIG, activation flags or arbitrary PYTHONPATH.
    keep = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "SYSTEMDRIVE",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "LANG",
        "LC_ALL",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    venv = output / "venv"
    env.update(
        {
            "UV_PROJECT_ENVIRONMENT": str(venv),
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_CACHE_DIR": str(output / "uv-cache"),
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(scratch),
            "TMP": str(scratch),
            "TMPDIR": str(scratch),
            "GIT_CONFIG_GLOBAL": str(git_config),
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    summary = {
        "source_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "steps": [],
        "integrations": {key: "not_run" for key in ("docker", "temporal", "model", "databases")},
    }

    def run(name, command, *, timeout=1800):
        print(f"[{name}] {' '.join(map(str, command))}", flush=True)
        started = time.monotonic()
        log = output / f"{name}.log"
        try:
            with log.open("w", encoding="utf-8") as stream:
                result = subprocess.run(
                    command,
                    cwd=checkout,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                )
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
        summary["steps"].append(
            {
                "name": name,
                "command": list(map(str, command)),
                "exit_code": code,
                "seconds": time.monotonic() - started,
            }
        )
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(log.read_text(encoding="utf-8")[-5000:], flush=True)
        return code

    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required; install the documented pinned version explicitly")
    for name, command in (
        ("python-version", [args.python, "--version"]),
        ("uv-version", [uv, "--version"]),
        ("lock", [uv, "lock", "--check", "--python", args.python]),
        ("sync", [uv, "sync", "--locked", "--group", "dev", "--python", args.python]),
    ):
        if run(name, command):
            return 1
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env.update(
        {
            "PATH": str(python.parent) + os.pathsep + env.get("PATH", ""),
            "PYTHONPATH": os.pathsep.join((str(checkout / "scripts/ci_support"), str(checkout))),
            "SPROUT_BASELINE_HOME": str(home),
            "SPROUT_BASELINE_OFFLINE": "1",
        }
    )
    pytest = [
        str(python),
        "-B",
        "-m",
        "pytest",
        "-p",
        "scripts.ci_support.pytest_gates",
        "-p",
        "no:cacheprovider",
        "--tb=short",
        "-o",
        "faulthandler_timeout=60",
    ]
    commands = [
        (
            "imports",
            [
                str(python),
                "-c",
                "import Sprout, Sprout.cli.app, web.webapi.app; "
                "import pytest, ruff; print('imports OK')",
            ],
        ),
        ("ruff", [str(python), "-m", "ruff", "check", ".", "--no-cache"]),
        (
            "collect",
            [
                *pytest,
                "--collect-only",
                "-q",
                "src/Sprout/tests",
                "web/tests",
                f"--junitxml={output / 'collect.xml'}",
                f"--gate-report={output / 'collect-gates.json'}",
            ],
        ),
        (
            "examples",
            [
                *pytest,
                "-q",
                "src/Sprout/tests/test_bubble_sort.py",
                "src/Sprout/tests/test_hello_world.py",
                f"--junitxml={output / 'examples.xml'}",
            ],
        ),
        (
            "r00",
            [
                *pytest,
                "-q",
                "src/Sprout/tests/test_skills_disclosure.py",
                f"--junitxml={output / 'r00.xml'}",
            ],
        ),
        (
            "l1",
            [
                *pytest,
                "-q",
                "src/Sprout/tests",
                "web/tests",
                "--baseline-l1",
                f"--basetemp={output / 'pytest-temp'}",
                f"--junitxml={output / 'l1.xml'}",
                f"--gate-report={output / 'l1-gates.json'}",
            ],
        ),
    ]
    failures = 0
    for name, command in commands:
        failures += bool(run(name, command))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
