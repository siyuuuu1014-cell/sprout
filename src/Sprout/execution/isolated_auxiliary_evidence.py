"""Frozen auxiliary plan and raw process inspection; no import or write authority."""

import json
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.execution.isolated_auxiliary import auxiliary_commands
from Sprout.execution.isolated_process_evidence import inspect_coding_process
from Sprout.execution.verification import classify_verification_command


async def inspect_auxiliary_evidence(metadata, *, context, node, baseline):
    commands = auxiliary_commands(node.metadata)
    contract = json.loads(json.dumps({"commands": commands, "baseline": baseline,
                                     "image": context.image}))
    identity = str(uuid5(NAMESPACE_URL, node.id + ":auxiliary-plan"))
    plan = await metadata.get_coding_intent(identity)
    if (not plan or plan.get("id") != identity or plan.get("task_id") != node.task_id
            or plan.get("node_id") != node.id
            or plan.get("owner") != node.metadata["execution_guard"]["owner"]
            or plan.get("contract") != contract or plan.get("signature") != fingerprint(contract)):
        raise ValueError("frozen_auxiliary_plan_mismatch")
    observed = []
    for index, (_, command) in enumerate(commands):
        directory = context.evidence / f"auxiliary-{index}"
        seal = directory / "baseline.json"
        if (directory.is_symlink() or seal.is_symlink() or not seal.is_file()
                or seal.stat().st_size > 8 * 1024 * 1024
                or json.loads(seal.read_text(encoding="utf-8")) != baseline):
            raise ValueError("auxiliary_baseline_seal_mismatch")
        request = replace(context, evidence=directory, exported=directory / "coding-input",
                          command=tuple(command), relative_cwd=".", read_only_input=False)
        process, record = await inspect_coding_process(
            metadata, request=request, node=node, slot=f"auxiliary-{index}", baseline=baseline)
        # The persisted process.command is the Docker launcher. Classify the
        # actual inner command, or pytest "no tests"/skips can look like success.
        outcome = classify_verification_command(replace(process, command=tuple(command)))
        if outcome != "passed":
            raise ValueError("auxiliary_check_not_passed:" + str(index) + ":" + outcome)
        observed.append(record)
    return {"contract": contract, "plan_fingerprint": plan["signature"], "observations": observed}
