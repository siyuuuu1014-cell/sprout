"""Bind exported observations to durable execution results, without granting trust."""

import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from Sprout.evolution.pytest_measurement import pytest_arguments, summarize
from Sprout.storage.local.sqlite.verification_report import execution_evidence


def provenance_blockers(results, reports) -> list[str]:
    try:
        for result, report in zip(results, reports, strict=True):
            evidence = execution_evidence(Path(result.evidence_dir) / "metadata.db", result.task_id)
            if not report or not report.get("records"):
                return ["execution_provenance_missing"]
            counters = {"test": 0, "regression": 0}
            for observed in report["records"]:
                kind = observed["kind"]
                slot = f"{kind}-{counters[kind]}"
                counters[kind] += 1
                identity = str(uuid5(NAMESPACE_URL, result.task_id + ":verification:" + slot))
                saved = evidence.get(identity)
                if not saved or saved["state"] != "completed":
                    return ["execution_provenance_not_completed"]
                record, output = saved["record"], saved["result"]
                if record.get("schema_version") != 2:
                    return ["execution_provenance_version_unsupported"]
                process = output["process"]
                command = record["command"]
                arguments = pytest_arguments(tuple(observed["command"]))
                nonce = record["nonce"]
                marker = "SPROUT_PYTEST_REPORT " + nonce + " "
                lines = [line[len(marker):] for line in process["stdout"].splitlines()
                         if line.startswith(marker)]
                if len(lines) != 1:
                    return ["execution_provenance_payload_invalid"]
                summary = summarize(json.loads(lines[0]), nonce, process["exit_code"],
                                    record["observer_hash"])
                if (record["slot"] != slot or observed.get("execution_id") != identity
                        or nonce != uuid5(NAMESPACE_URL, result.task_id + ":" + slot).hex
                        or output.get("execution_id") != identity
                        or output.get("waiting") is not False
                        or arguments is None
                        or command[-(len(arguments) + 2):] != ["-", nonce, *arguments]
                        or observed.get("verification_image") not in command
                        or list(process["command"]) != command
                        or observed.get("executed_command") != command
                        or any(observed.get(key) != value for key, value in process.items()
                               if key != "command")
                        or observed.get("container_name") != record["container_name"]
                        or output.get("container_name") != record["container_name"]
                        or output.get("input_unchanged") is not True
                        or observed.get("input_unchanged") is not True
                        or summary != output.get("pytest_observation")
                        or summary != observed.get("pytest_observation")
                        or "observer_identity_mismatch" in summary["blockers"]):
                    return ["execution_provenance_binding_mismatch"]
        return []
    except (ValueError, OSError, KeyError, TypeError, AttributeError, IndexError):
        return ["execution_provenance_missing_or_malformed"]
