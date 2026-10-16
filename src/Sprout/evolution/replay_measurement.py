"""Independent process observations; not by themselves a trusted growth verdict."""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import asdict
from pathlib import Path

from Sprout.execution.verification import classify_verification_command

COMPLETED_MEASUREMENT = "independent-measurement-completed.json"


def command_parts(command: str) -> tuple[str, ...]:
    parts = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        parts = [
            part[1:-1] if part[:1] == part[-1:] and part[:1] in {"'", '"'} else part
            for part in parts
        ]
    if not parts:
        raise ValueError("Measurement command must not be empty")
    return tuple(parts)


async def measure(
    runtime,
    task_id: str,
    tests: tuple[str, ...],
    regressions: tuple[str, ...],
    *,
    evidence_dir: Path | None = None,
):
    """Run the operator's frozen commands once through the existing policy broker.

    No shell expansion, automatic retry, approval grant or success inference from
    the task status. A withheld/unknown outcome stops further commands.
    """
    records = []
    for kind, commands in (("test", tests), ("regression", regressions)):
        for command in commands:
            parts = command_parts(command)
            from Sprout.evolution.pytest_measurement import observe

            observation = (
                await observe(runtime, task_id, parts, evidence_dir)
                if evidence_dir is not None
                else None
            )
            if observation is None:
                result = await runtime.run_task_process(task_id, parts, timeout_seconds=60)
                extra = {}
            else:
                result, summary, report_path, actual = observation
                extra = {
                    "pytest_observation": summary,
                    "pytest_report_path": report_path,
                    "executed_command": actual,
                }
            outcome = classify_verification_command(result)
            if (
                observation is not None
                and not summary["observed_all_passed"]
                and outcome == "passed"
            ):
                outcome = "incomplete_pytest_observation"
            records.append(
                {"kind": kind, "outcome": outcome, **asdict(result), "command": parts, **extra}
            )
            if not result.allowed or result.exit_code is None:
                return records
    return records


def write_evidence(directory: Path, payload: dict) -> str:
    """Exclusive creation prevents accidentally replacing a previous observation."""
    path = directory / "independent-measurement.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
    return str(path)
