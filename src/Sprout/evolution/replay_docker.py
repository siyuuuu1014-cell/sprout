"""Docker-only independent measurements, separate from generic task execution."""

import json
from dataclasses import asdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from Sprout.evolution import pytest_probe
from Sprout.evolution.pytest_measurement import pytest_arguments, summarize
from Sprout.evolution.replay_measurement import command_parts
from Sprout.evolution.replay_oracle import integrity
from Sprout.execution.models import ProcessResult
from Sprout.execution.verification import classify_verification_command
from Sprout.execution.verification_backend import VerificationRequest
from Sprout.execution.verification_export import export_input, private_resource
from Sprout.execution.verification_run import DockerVerificationRun, input_manifest


async def measure_docker(
    runtime,
    task_id,
    tests,
    regressions,
    *,
    directory,
    repo,
    original,
    image,
    oracle_paths,
    frozen,
    classify_overrides=None,
):
    exported = directory / "verification-input"
    seal = directory / "verification-input-manifest.json"
    if not exported.exists():
        manifest = export_input(repo, exported, original, classify_overrides=classify_overrides)
        with seal.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
    if not seal.exists() or input_manifest(exported) != json.loads(
        seal.read_text(encoding="utf-8")
    ):
        raise ValueError("Export is incomplete or changed; cannot reuse partial input")
    if not integrity(exported, oracle_paths, frozen):
        raise ValueError("Exported oracle does not match the frozen original")
    if any(
        private_resource(path, exported, classify_overrides) for path in input_manifest(exported)
    ):
        raise ValueError("Export classification changed to private/data")
    runner = DockerVerificationRun(runtime.storage.metadata, summarize)
    records = []
    for kind, commands in (("test", tests), ("regression", regressions)):
        for index, command in enumerate(commands):
            parts = command_parts(command)
            arguments = pytest_arguments(parts)
            if arguments is None:
                raise ValueError("Independent Docker measurement only supports pytest")
            slot = f"{kind}-{index}"
            nonce = uuid5(NAMESPACE_URL, task_id + ":" + slot).hex
            request = VerificationRequest(
                task_id,
                original,
                exported,
                Path(pytest_probe.__file__).resolve(),
                image,
                ("-", nonce, *arguments),
            )
            output = await runner.execute(runtime, request, slot=slot)
            if output.get("waiting"):
                records.append(
                    {
                        "kind": kind,
                        "command": list(parts),
                        "allowed": False,
                        "exit_code": None,
                        "outcome": "waiting_resource",
                        **output,
                    }
                )
                return records
            process = ProcessResult(
                **{**output["process"], "command": tuple(output["process"]["command"])}
            )
            observed = output["pytest_observation"]
            outcome = classify_verification_command(process)
            if not output["input_unchanged"] or (
                outcome == "passed" and not observed["observed_all_passed"]
            ):
                outcome = "incomplete_pytest_observation"
            records.append(
                {
                    **asdict(process),
                    "kind": kind,
                    "command": list(parts),
                    "executed_command": output["process"]["command"],
                    "outcome": outcome,
                    "pytest_observation": observed,
                    "execution_id": output["execution_id"],
                    "verification_image": image,
                    "input_unchanged": output["input_unchanged"],
                    "container_name": output["container_name"],
                }
            )
            if not output["input_unchanged"]:
                return records
    return records
