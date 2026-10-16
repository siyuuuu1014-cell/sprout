"""Node-owned isolated JSON acceptance; expected outputs never enter containers."""

import hashlib
import json
from dataclasses import asdict, replace
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.coding_backend import DockerCodingBackend
from Sprout.execution.function_verification import judge_actual
from Sprout.execution.isolated_case_contract import case_command, planned_cases
from Sprout.execution.isolated_case_contract import public_demo as public_demo
from Sprout.execution.models import TestResult
from Sprout.execution.verification_run import input_manifest
from Sprout.strategy.acceptance import AcceptanceSpec


async def verify_isolated_cases(metadata, runner, *, context, node, acceptance_spec,
                                regression_spec, phase="evaluation"):
    if phase not in {"baseline", "evaluation"}:
        raise ValueError("Explicit isolated verification phase required")
    owner = node.metadata.get("execution_guard", {}).get("owner")
    if not owner or node.task_id != context.task_id:
        raise ValueError("Live owning node required")
    acceptance = AcceptanceSpec.from_payload(acceptance_spec)
    if regression_spec is None and phase == "evaluation":
        return {"waiting": True, "waiting_kind": "configuration",
                "reason": "frozen_regression_spec_required", "verified": False}
    regression = AcceptanceSpec.from_payload(regression_spec) if regression_spec else None
    contract = {"acceptance": acceptance.to_payload(),
                "regression": regression.to_payload() if regression else None,
                "phase": phase, "input_manifest": input_manifest(context.exported),
                "image": context.image}
    await metadata.ensure_coding_intent({
        "id": str(uuid5(NAMESPACE_URL, node.id + ":case-plan:" + phase)),
        "task_id": context.task_id, "node_id": node.id, "owner": owner,
        "signature": hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest(),
        "contract": contract, "schema_version": 1,
    })
    cases, demo_source = planned_cases(acceptance, regression, phase)
    demo_path = context.evidence / (phase + "-public-demo.py")
    if demo_path.exists():
        if demo_path.is_symlink() or demo_path.read_text(encoding="utf-8") != demo_source:
            raise ValueError("Frozen public demo changed")
    else:
        with demo_path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(demo_source)
    before = input_manifest(context.exported)
    records, results = [], []
    for kind, index, case in cases:
        command = case_command(kind, case, demo_source)
        request = replace(context, command=command, relative_cwd=".", read_only_input=True)
        backend = DockerCodingBackend(metadata, runner, node_id=node.id, owner=owner,
                                     command_slot=f"{phase}-{kind}-{index}")
        process = await backend.execute(request, node_id=node.id)
        if process.needs_approval:
            return {"waiting": True, "waiting_kind": "approval",
                    "pending_approvals": [process.approval_id], "verified": False}
        try:
            judgement = judge_actual(process, case.expected_output)
        except (ValueError, TypeError):
            judgement = {"passed": False, "reason": "invalid_actual_output"}
        if input_manifest(context.exported) != before:
            raise RuntimeError("Read-only acceptance input changed")
        name = f"{kind}:{case.id}: isolated-json" if kind != "poc" else "poc: isolated-public-demo"
        results.append(TestResult(name, judgement["passed"], executed=process.allowed))
        records.append({"kind": kind, "case_id": case.id, "name": name,
                        "passed": judgement["passed"], "exit_code": process.exit_code,
                        "allowed": process.allowed, "outcome": "passed"
                        if judgement["passed"] else "check_failed", "command": list(command)})
    passed = all(item.executed and item.passed for item in results)
    return {"waiting": False, "verified": passed, "status": "passed" if passed else "failed",
            "results": records, "test_results": [asdict(item) for item in results],
            "acceptance_spec": acceptance.to_payload(), "demo_path": str(demo_path),
            "demo_hash": hashlib.sha256(demo_source.encode()).hexdigest(),
            "validation_committed": False}
