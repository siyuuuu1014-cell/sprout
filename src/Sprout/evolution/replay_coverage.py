"""Compare structured observations and bind the existing Python acceptance oracle."""

from hashlib import sha256

from Sprout.execution.acceptance import render_acceptance_tests
from Sprout.strategy.acceptance import AcceptanceSpec


def acceptance_coverage(report: dict) -> dict:
    try:
        spec = AcceptanceSpec.from_payload(report.get("acceptance_spec"))
    except (ValueError, TypeError):
        return {"covered": False, "blockers": ["frozen_acceptance_spec_missing"]}
    expected = sha256(render_acceptance_tests(spec).encode()).hexdigest()
    paths = [
        path for path, value in (report.get("oracle_snapshot") or {}).items() if value == expected
    ]
    observed = {
        nodeid
        for record in report.get("records", ())
        for nodeid in record.get("pytest_observation", {}).get("executed_nodeids", ())
    }
    missing = [
        case.id
        for case in spec.cases
        if not any(f"{path}::test_{case.id}" in observed for path in paths)
    ]
    return {
        "covered": bool(paths) and not missing,
        "missing_cases": missing,
        "matched_oracle_files": paths,
        "blockers": ["acceptance_oracle_not_collected"] if missing or not paths else [],
    }


def compare_structured(left: dict | None, right: dict | None) -> dict:
    blockers = []
    if not left or not right:
        return {"comparable": False, "blockers": ["measurement_missing"]}
    if left.get("acceptance_spec") != right.get("acceptance_spec"):
        blockers.append("acceptance_spec_mismatch")
    pairs = zip(left.get("records", ()), right.get("records", ()), strict=False)
    if len(left.get("records", ())) != len(right.get("records", ())):
        blockers.append("record_count_mismatch")
    if not left.get("records") or not right.get("records"):
        blockers.append("records_missing")
    for index, (a, b) in enumerate(pairs):
        if (a.get("verification_image") or b.get("verification_image")) and (
            a.get("verification_image") != b.get("verification_image")
            or a.get("input_unchanged") is not True
            or b.get("input_unchanged") is not True
        ):
            blockers.append(f"container_environment_mismatch:{index}")
        x, y = a.get("pytest_observation"), b.get("pytest_observation")
        if not x or not y or not x.get("nodeids") or not y.get("nodeids"):
            blockers.append(f"structured_collection_missing:{index}")
            continue
        if sorted(x["nodeids"]) != sorted(y["nodeids"]):
            blockers.append(f"collected_test_set_mismatch:{index}")
        fields = ("python", "executable", "pytest_version", "pytest_module", "probe_hash")
        if any(
            not x.get("runner", {}).get(key) or x["runner"][key] != y.get("runner", {}).get(key)
            for key in fields
        ):
            blockers.append(f"runner_identity_mismatch:{index}")
        for observation, report in ((x, left), (y, right)):
            config = observation.get("configuration", {})
            path = config.get("relative_config")
            if not config.get("plugins") or (
                config.get("inipath")
                and (
                    not path
                    or (report.get("oracle_snapshot") or {}).get(path) != config.get("config_hash")
                )
            ):
                blockers.append(f"unfrozen_configuration:{index}")
        fields = ("relative_config", "config_hash", "plugins")
        if any(
            x.get("configuration", {}).get(key) != y.get("configuration", {}).get(key)
            for key in fields
        ):
            blockers.append(f"configuration_mismatch:{index}")
    coverage = [acceptance_coverage(report) for report in (left, right)]
    if not all(item["covered"] for item in coverage):
        blockers.append("acceptance_coverage_incomplete")
    return {
        "comparable": not blockers,
        "blockers": sorted(set(blockers)),
        "acceptance_coverage": coverage,
        "trusted_execution": False,
    }
