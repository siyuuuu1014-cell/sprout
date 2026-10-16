"""Read-only rejudgment of function actuals from durable execution evidence."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from Sprout.evolution.regression_spec import frozen
from Sprout.execution.function_verification import judge_actual
from Sprout.execution.models import ProcessResult
from Sprout.storage.local.sqlite.verification_report import execution_evidence
from Sprout.strategy.acceptance import AcceptanceSpec


def function_provenance(results, reports, *, purpose="acceptance"):
    outcomes = []
    try:
        for result, report in zip(results, reports, strict=True):
            payload = (frozen(Path(result.evidence_dir), report.get("regression_fingerprint"))
                       if purpose == "regression" else report.get("acceptance_spec"))
            spec = AcceptanceSpec.from_payload(payload)
            records = report.get("regression_records" if purpose == "regression"
                                 else "function_records", [])
            evidence = execution_evidence(Path(result.evidence_dir) / "metadata.db", result.task_id)
            if len(records) != len(spec.cases):
                raise ValueError("Incomplete function cases")
            passed = True
            for case, observed in zip(spec.cases, records, strict=True):
                identity = str(uuid5(
                    NAMESPACE_URL, result.task_id + ":function-" + purpose + ":" + case.id))
                saved = evidence[identity]
                record, output = saved["record"], saved["result"]
                signature = hashlib.sha256(json.dumps(
                    record["contract"], sort_keys=True, default=str).encode()).hexdigest()
                if (saved["state"] != "completed" or record.get("schema_version") != 3
                        or record.get("signature") != signature
                        or record["contract"].get("purpose") != purpose
                        or output.get("purpose") != purpose
                        or record["contract"]["request"]["task_id"] != result.task_id
                        or record.get("protocol") != "function_json_v1"
                        or record["contract"]["case"] != {**asdict(case), "args": list(case.args)}
                        or record["contract"]["request"]["image"] != observed["verification_image"]
                        or observed != output or output.get("execution_id") != identity
                        or output.get("case_id") != case.id or output.get("waiting") is not False
                        or output.get("input_unchanged") is not True
                        or output["process"]["command"] != record["command"]
                        or output.get("container_name") != record["container_name"]):
                    raise ValueError("Function binding mismatch")
                process = ProcessResult(**{**output["process"],
                                           "command": tuple(output["process"]["command"])})
                judgement = judge_actual(process, case.expected_output)
                if judgement != output["observation"] or (
                    judgement["passed"] != output.get("host_oracle_passed")
                ):
                    raise ValueError("Function oracle mismatch")
                passed = passed and judgement["passed"]
            outcomes.append(passed)
        return {"verified": True, "observed_passed": outcomes, "blockers": []}
    except (ValueError, TypeError, KeyError, OSError, AttributeError):
        return {"verified": False, "observed_passed": [],
                "blockers": ["function_execution_evidence_missing_or_mismatched"]}
