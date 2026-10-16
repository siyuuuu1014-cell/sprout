"""Additional checks in disposable, independent writable exports; never import."""

import hashlib
import json
import shlex
from dataclasses import asdict, replace
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.coding_backend import DockerCodingBackend
from Sprout.execution.models import TestResult
from Sprout.execution.verification import classify_verification_command
from Sprout.execution.verification_export import export_input
from Sprout.execution.verification_run import input_manifest


def auxiliary_commands(metadata):
    """Strict normalization: malformed mandatory checks are never skipped."""
    planned = []
    for key, kind in (("test_commands", "test"), ("build_commands", "build"),
                      ("verification_commands", "acceptance")):
        values = metadata.get(key, ())
        if not isinstance(values, (list, tuple)):
            raise ValueError("Auxiliary commands must be a list")
        for value in values:
            if key == "verification_commands":
                if not isinstance(value, dict):
                    raise ValueError("Structured verification criterion required")
                label, value = value.get("kind", kind), value.get("command")
            else:
                label = kind
            if not isinstance(label, str) or not label.strip():
                raise ValueError("Auxiliary criterion kind required")
            if isinstance(value, str):
                parts = tuple(shlex.split(value))
            elif isinstance(value, (list, tuple)):
                parts = tuple(value)
            else:
                raise ValueError("Structured command or quoted command string required")
            if not parts or any(not isinstance(p, str) or not p or "\x00" in p for p in parts):
                raise ValueError("Nonempty auxiliary command required")
            planned.append((label, parts))
    return planned


async def run_auxiliary_checks(metadata, runner, *, context, node, commands):
    owner = node.metadata.get("execution_guard", {}).get("owner")
    if not owner or node.task_id != context.task_id:
        raise ValueError("Live owning node required")
    before = input_manifest(context.exported)
    contract = {"commands": commands, "baseline": before, "image": context.image}
    await metadata.ensure_coding_intent({
        "id": str(uuid5(NAMESPACE_URL, node.id + ":auxiliary-plan")),
        "task_id": context.task_id, "node_id": node.id, "owner": owner,
        "signature": hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest(),
        "contract": json.loads(json.dumps(contract)), "schema_version": 1})
    results, records = [], []
    for index, (kind, command) in enumerate(commands):
        directory = context.evidence / f"auxiliary-{index}"
        if directory.is_symlink():
            raise ValueError("Linked auxiliary directory forbidden")
        directory.mkdir(exist_ok=True)
        exported = directory / "coding-input"
        seal = directory / "baseline.json"
        if not exported.exists():
            if seal.exists():
                raise ValueError("Partial auxiliary export cannot be recreated")
            actual = export_input(context.exported, exported, context.original,
                                  exclude_paths=context.oracle_paths)
            if actual != before:
                raise ValueError("Auxiliary input changed while exporting")
            with seal.open("x", encoding="utf-8") as stream:
                json.dump(before, stream, sort_keys=True)
        if (seal.is_symlink() or not seal.is_file() or seal.stat().st_size > 8 * 1024 * 1024
                or json.loads(seal.read_text(encoding="utf-8")) != before
                or input_manifest(exported) != before):
            raise ValueError("Auxiliary export changed; cannot silently rebuild")
        request = replace(context, evidence=directory, exported=exported,
                          command=tuple(command), relative_cwd=".", read_only_input=False)
        process = await DockerCodingBackend(metadata, runner, node_id=node.id, owner=owner,
                                           command_slot=f"auxiliary-{index}").execute(
                                               request, node_id=node.id)
        if input_manifest(context.exported) != before:
            raise RuntimeError("Auxiliary command changed shared verification input")
        if process.needs_approval:
            return {"waiting": True, "waiting_kind": "approval",
                    "pending_approvals": [process.approval_id], "verified": False}
        outcome = classify_verification_command(replace(process, command=tuple(command)))
        if process.error or process.redactions:
            outcome = "runner_error"
        passed = outcome == "passed"
        name = f"{kind}: {' '.join(command)}"
        output = (process.stdout + "\n" + process.stderr + "\n" + (process.error or ""))[:65536]
        results.append(TestResult(name, bool(passed), output=output,
                                  duration_ms=process.duration_ms, executed=process.allowed))
        records.append({"kind": kind, "name": name, "command": list(command),
                        "allowed": process.allowed, "exit_code": process.exit_code,
                        "passed": bool(passed), "output": output,
                        "outcome": outcome})
    passed = all(result.executed and result.passed for result in results)
    return {"waiting": False, "verified": passed, "status": "passed" if passed else "failed",
            "results": records, "test_results": [asdict(result) for result in results],
            "validation_committed": False}
