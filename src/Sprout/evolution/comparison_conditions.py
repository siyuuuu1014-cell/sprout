"""Bind observations to independently persisted experiment plans, read-only."""

import re
from pathlib import Path

from Sprout.evolution.replay_plan import verify_plan
from Sprout.storage.local.sqlite.verification_report import recovery_facts


def condition_blockers(results, reports) -> list[str]:
    plans = []
    input_fingerprints = []
    try:
        for result, report in zip(results, reports, strict=True):
            if not report:
                return ["experiment_plan_evidence_missing"]
            directory = Path(result.evidence_dir).resolve(strict=True)
            facts = recovery_facts(directory / "metadata.db", result.task_id)
            input_fingerprints.append(facts.get("task_input_fingerprint"))
            plan = verify_plan(facts["metadata"].get("replay_plan"))
            if plan["schema_version"] not in {4, 5}:
                return ["experiment_plan_version_unsupported"]
            if (facts["status"] != "completed" or plan["execution_mode"] != "code"
                    or facts.get("source") != "cli"
                    or any(facts.get("actor", {}).get(key) != plan.get("creator", {}).get(key)
                           for key in ("user_id", "authenticated", "source"))
                    or facts["metadata"].get("execution_mode") != "code"
                    or Path(facts["workspace_root"]).resolve() != directory / "repo"
                    or Path(plan["repository_root"]) != directory / "repo"
                    or plan["source_task_id"] != report["source_task_id"]
                    or plan["revision"] != report["revision"]
                    or plan["test_commands"] != report["test_commands"]
                    or plan["regression_commands"] != report["regression_commands"]
                    or plan["oracle_snapshot"] != report["oracle_snapshot"]
                    or facts["metadata"].get("acceptance_spec") != report.get("acceptance_spec")
                    or facts["metadata"].get("regression_fingerprint")
                    != report.get("regression_fingerprint")
                    or any(record.get("verification_image") != plan["verification_image"]
                           for record in report["records"])):
                return ["experiment_plan_measurement_binding_mismatch"]
            plans.append(plan)
        left, right = plans
        blockers = []
        if left.get("coding_profile") != right.get("coding_profile"):
            blockers.append("experiment_coding_profile_mismatch")
        if not input_fingerprints[0] or input_fingerprints[0] != input_fingerprints[1]:
            blockers.append("experiment_task_inputs_mismatch")
        config_fields = {"schema_version", "model_fingerprint", "security_fingerprint",
                         "tools_fingerprint", "execution_fingerprint"}
        for plan in plans:
            config = plan.get("configuration", {})
            creator = plan.get("creator", {})
            if (set(config) != config_fields or config.get("schema_version") != 1
                    or any(not re.fullmatch(r"[0-9a-f]{64}", str(config.get(key, "")))
                           for key in config_fields - {"schema_version"})):
                blockers.append("experiment_configuration_contract_invalid")
            if (creator.get("authenticated") is not True or creator.get("source") != "cli"
                    or not creator.get("user_id")):
                blockers.append("experiment_creator_not_verified")
        for field in ("configuration", "creator", "original_root", "oracle_paths"):
            if not left.get(field) or left[field] != right.get(field):
                blockers.append("experiment_" + field + "_mismatch")
        if left.get("candidate") is not None or not right.get("candidate"):
            blockers.append("candidate_plan_binding_mismatch")
        else:
            snapshot = right["candidate"]
            if (snapshot["artifact_id"] != reports[1]["candidate_id"]
                    or snapshot["content_hash"] != reports[1]["candidate_hash"]
                    or vars(results[1].artifact_snapshot) != snapshot):
                blockers.append("candidate_plan_binding_mismatch")
        a, b = left["runtime_snapshot"], right["runtime_snapshot"]
        if a.get("schema_version") != 2 or b.get("schema_version") != 2:
            blockers.append("runtime_snapshot_version_unsupported")
        for field in ("implementation_fingerprint", "tools_fingerprint"):
            if not a.get(field) or a[field] != b.get(field):
                blockers.append("experiment_" + field + "_mismatch")
        if a.get("external_tool_implementation") is not False or b.get(
                "external_tool_implementation") is not False:
            blockers.append("external_tool_implementation_not_verified")
        x, y = a.get("skill_manifest"), b.get("skill_manifest")
        if not isinstance(x, dict) or not isinstance(y, dict):
            blockers.append("per_skill_snapshot_missing")
        else:
            changed = [name for name in x.keys() | y.keys() if x.get(name) != y.get(name)]
            if len(changed) != 1 or changed[0] not in y or not right.get("candidate"):
                blockers.append("candidate_not_only_skill_difference")
            elif (y[changed[0]]["instructions_hash"] != right["candidate"]["content_hash"]
                  or y[changed[0]]["version"] != right["candidate"]["artifact_version"]):
                blockers.append("candidate_skill_snapshot_mismatch")
        return blockers
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        return ["experiment_plan_evidence_unavailable_or_malformed"]
