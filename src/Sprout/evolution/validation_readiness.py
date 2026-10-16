"""Internal read-only gate projection; never an imported-report authorization API."""


def measurement_readiness(*, binding_blockers, condition_blockers, execution_blockers,
                          pytest_outcomes, functions, regressions):
    blockers = [*binding_blockers, *condition_blockers, *execution_blockers]
    if len(pytest_outcomes) != 2 or any(value is not True for value in pytest_outcomes):
        blockers.append("paired_pytest_observations_not_all_passed")
    for name, evidence in (("function", functions), ("regression", regressions)):
        blockers.extend(evidence.get("blockers", []))
        if evidence.get("verified") is not True:
            blockers.append(f"{name}_execution_provenance_not_verified")
        outcomes = evidence.get("observed_passed")
        if (not isinstance(outcomes, list) or len(outcomes) != 2
                or any(value is not True for value in outcomes)):
            blockers.append(f"paired_{name}_cases_not_all_passed")
    return {"observation_gates_passed": not blockers,
            "measurement_blockers": sorted(set(blockers)),
            "validation_authorized": False,
            "validation_blockers": sorted(set(
                [*blockers, "trusted_measurement_handoff_not_established"])),
            "validation_committed": False}
