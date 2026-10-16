"""Structured pytest observations with fail-closed completeness checks."""

import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4


def pytest_arguments(parts: tuple[str, ...]) -> tuple[str, ...] | None:
    name = Path(parts[0]).name.lower()
    if name in {"pytest", "pytest.exe"}:
        return parts[1:]
    if name in {"python", "python.exe", "python3", "python3.exe"} and parts[1:3] == (
        "-m",
        "pytest",
    ):
        return parts[3:]
    return None


def summarize(payload: dict, nonce: str, exit_code: int | None, probe_hash: str) -> dict:
    collected = payload.get("collected", [])
    reports = payload.get("reports", [])
    runner = payload.get("runner", {})
    blockers = []
    if (
        payload.get("schema_version") != 1
        or payload.get("nonce") != nonce
        or payload.get("exit_code") != exit_code
        or runner.get("probe_hash") != probe_hash
    ):
        blockers.append("observer_identity_mismatch")
    if not collected or len(collected) != len(set(collected)):
        blockers.append("empty_or_duplicate_collection")
    if payload.get("collection_errors"):
        blockers.append("collection_errors")
    counts = {"passed": 0, "failed": 0, "skipped": 0, "incomplete": 0}
    for nodeid in collected:
        phases = [item for item in reports if item.get("nodeid") == nodeid]
        if any(item.get("outcome") == "failed" for item in phases):
            counts["failed"] += 1
        elif any(item.get("outcome") == "skipped" or item.get("wasxfail") for item in phases):
            counts["skipped"] += 1
        elif (
            len(phases) == 3
            and {item.get("phase") for item in phases} == {"setup", "call", "teardown"}
            and all(item.get("outcome") == "passed" for item in phases)
        ):
            counts["passed"] += 1
        else:
            counts["incomplete"] += 1
    if any(item.get("nodeid") not in collected for item in reports):
        blockers.append("uncollected_execution")
    if exit_code != 0 or counts["failed"] or counts["skipped"] or counts["incomplete"]:
        blockers.append("not_all_collected_tests_passed")
    return {
        "collected_count": len(collected),
        "counts": counts,
        "nodeids": collected,
        "executed_nodeids": [
            nodeid
            for nodeid in collected
            if any(
                item.get("nodeid") == nodeid
                and item.get("phase") == "call"
                and item.get("outcome") in {"passed", "failed"}
                for item in reports
            )
        ],
        "runner": runner,
        "configuration": payload.get("configuration", {}),
        "blockers": blockers,
        "observed_all_passed": not blockers,
        "trusted_execution": False,
    }


async def observe(runtime, task_id: str, parts: tuple[str, ...], directory: Path):
    arguments = pytest_arguments(parts)
    if arguments is None:
        return None
    from Sprout.evolution import pytest_probe

    probe = Path(pytest_probe.__file__).resolve()
    probe_hash = hashlib.sha256(probe.read_bytes()).hexdigest()
    nonce = uuid4().hex
    output = directory / f"pytest-observation-{nonce}.json"
    # Host interpreter and observer live outside the candidate checkout.
    actual = (sys.executable, "-I", str(probe), str(output), nonce, *arguments)
    result = await runtime.run_task_process(task_id, actual, timeout_seconds=60)
    summary = {
        "observed_all_passed": False,
        "trusted_execution": False,
        "blockers": ["structured_report_missing"],
    }
    if result.allowed and result.exit_code is not None and output.exists():
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
            summary = summarize(payload, nonce, result.exit_code, probe_hash)
        except (ValueError, TypeError, AttributeError, KeyError):
            summary["blockers"] = ["structured_report_invalid"]
    return result, summary, str(output), actual
