"""Read-only source-bound node observation inspection, never write/publication authority."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from Sprout.evolution.regression_spec import frozen
from Sprout.evolution.replay_plan import verify_plan
from Sprout.execution.acceptance import acceptance_demo_path, acceptance_test_path
from Sprout.execution.coding_container import CodingCommand, coding_node_directory_name
from Sprout.execution.function_verification import judge_actual
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.execution.isolated_auxiliary_evidence import inspect_auxiliary_evidence
from Sprout.execution.isolated_case_contract import case_command, planned_cases
from Sprout.execution.isolated_process_evidence import inspect_coding_process
from Sprout.execution.verification_export import export_input
from Sprout.execution.verification_run import input_manifest
from Sprout.orchestration.models import NodeStatus, NodeType
from Sprout.strategy.acceptance import AcceptanceSpec


async def inspect_commit_evidence(metadata, *, task_id, evaluation_node_id, planner, git_plan):
    """Rejudge persisted raw actuals; a report's passed flag is never evidence.

    This checks observation consistency, not Docker commissioning, current
    configuration compatibility or OS trust.
    The write coordinator must not authorize writes from this result alone.
    """
    try:
        evidence = await _inspect(metadata, task_id, evaluation_node_id, planner, git_plan)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as exc:
        return {"evidence_binding_passed": False, "write_authorized": False,
                "validation_authorized": False, "blockers": [str(exc)]}
    return {"evidence_binding_passed": True, "write_authorized": False,
            "validation_authorized": False, "evidence_fingerprint": fingerprint(evidence),
            "blockers": ["isolated_git_runtime_commissioning_not_established",
                         "isolated_git_write_coordinator_not_assembled"]}


async def _inspect(metadata, task_id, node_id, planner, git_plan):
    task = await metadata.get_task(task_id)
    node = await metadata.get_execution_node(node_id)
    if (task is None or node is None or node.task_id != task_id or node.type != NodeType.EVALUATION
            or node.status != NodeStatus.COMPLETED
            or node.metadata.get("execution_guard", {}).get("state") != "completed"):
        raise ValueError("completed_guarded_evaluation_required")
    payload = verify_plan(task.metadata.get("replay_plan"))
    workspace = await metadata.get_workspace(task.workspace_id)
    if (payload["schema_version"] != 5 or payload["execution_mode"] != "code"
            or task.metadata.get("execution_mode") != "code" or workspace is None
            or workspace.root.resolve() != planner.repository_root):
        raise ValueError("creator_bound_coding_plan_required")
    if (Path(payload["repository_root"]).resolve() != planner.repository_root
            or Path(payload["original_root"]).resolve() != planner.original
            or git_plan != await planner.commit_plan(git_plan["source"])):
        raise ValueError("current_commit_plan_changed")
    node_directory = coding_node_directory_name(node_id)
    owner = node.metadata["execution_guard"].get("owner")
    export = await metadata.get_coding_intent(node_id + ":export")
    if (not export or export.get("task_id") != task_id or export.get("owner") != owner
            or export.get("node_id") != node_id):
        raise ValueError("persisted_evaluation_export_required")
    contract = export["contract"]
    if export.get("signature") != fingerprint(contract):
        raise ValueError("evaluation_export_signature_mismatch")
    expected_paths = tuple(dict.fromkeys((*payload["oracle_paths"], acceptance_test_path(task_id),
        acceptance_demo_path(task_id), acceptance_test_path(payload["source_task_id"]),
        acceptance_demo_path(payload["source_task_id"]))))
    image = payload["coding_profile"]["image"]
    if (contract["task_id"] != task_id or contract["node_id"] != node_id
            or Path(contract["target"]).resolve() != Path(git_plan["source"]).resolve()
            or contract["image"] != image or tuple(contract["oracle_paths"]) != expected_paths):
        raise ValueError("evaluation_export_contract_mismatch")
    coding_evidence = planner.evidence / "coding-evidence"
    directory = coding_evidence / node_directory
    seal = directory / "coding-baseline.json"
    if (coding_evidence.is_symlink() or directory.is_symlink()
            or seal.is_symlink() or not seal.is_file()
            or seal.stat().st_size > 8 * 1024 * 1024
            or json.loads(seal.read_text(encoding="utf-8")) != contract):
        raise ValueError("evaluation_export_seal_mismatch")
    context = CodingCommand(task_id, planner.original, directory, directory / "coding-input",
                            image, ("python",), expected_paths)
    manifest = export_input(Path(git_plan["source"]), planner.evidence / "handoff-preview",
                            planner.original, exclude_paths=expected_paths, manifest_only=True)
    if manifest != contract["baseline"] or input_manifest(context.exported) != manifest:
        raise ValueError("measured_source_changed")
    acceptance = AcceptanceSpec.from_payload(task.metadata.get("acceptance_spec"))
    regression = AcceptanceSpec.from_payload(frozen(planner.evidence,
                                                    task.metadata.get("regression_fingerprint")))
    case_contract = {"acceptance": acceptance.to_payload(), "regression": regression.to_payload(),
                     "phase": "evaluation", "input_manifest": manifest, "image": image}
    case_id = str(uuid5(NAMESPACE_URL, node_id + ":case-plan:evaluation"))
    case_plan = await metadata.get_coding_intent(case_id)
    if (not case_plan or case_plan.get("task_id") != task_id or case_plan.get("owner") != owner
            or case_plan.get("node_id") != node_id
            or case_plan.get("contract") != case_contract
            or case_plan.get("signature") != fingerprint(case_contract)):
        raise ValueError("frozen_case_plan_mismatch")
    cases, demo_source = planned_cases(acceptance, regression, "evaluation")
    observed = []
    for kind, index, case in cases:
        slot = f"evaluation-{kind}-{index}"
        request = replace(context, command=case_command(kind, case, demo_source),
                          read_only_input=True)
        process, observation = await inspect_coding_process(
            metadata, request=request, node=node, slot=slot, baseline=manifest)
        if judge_actual(process, case.expected_output)["passed"] is not True:
            raise ValueError("case_actual_does_not_match:" + slot)
        observed.append(observation)
    auxiliary = await inspect_auxiliary_evidence(metadata, context=context, node=node,
                                                 baseline=manifest)
    if (input_manifest(context.exported) != manifest
            or git_plan != await planner.commit_plan(git_plan["source"])):
        raise ValueError("source_changed_during_handoff")
    return {"task_id": task_id, "node_id": node_id, "git_plan": git_plan,
            "case_contract": case_contract, "observations": observed, "auxiliary": auxiliary}
