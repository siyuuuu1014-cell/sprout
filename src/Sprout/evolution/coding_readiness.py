"""Read-only inventory of isolated runtime readiness; not an execution capability."""

from Sprout.evolution.replay_config import configuration_blockers
from Sprout.evolution.replay_plan import verify_plan
from Sprout.evolution.replay_snapshot import snapshot_blockers
from Sprout.gateway.local_identity import creator_blockers


def inspect_coding_readiness(runtime, *, plan, settings, principal):
    """Never start Runtime, inspect Docker, request approval, or install adapters."""
    try:
        payload = verify_plan(plan)
    except (ValueError, TypeError, KeyError):
        return _report(["coding_replay_plan_invalid"])
    if payload["schema_version"] != 5 or payload["execution_mode"] != "code":
        return _report(["creator_bound_coding_plan_required"])
    blockers = creator_blockers(payload.get("creator"), principal)
    blockers.extend(configuration_blockers(payload.get("configuration"), settings))
    blockers.extend(snapshot_blockers(payload.get("runtime_snapshot"), runtime))
    if runtime is None:
        return _report([*blockers, "isolated_runtime_required"])
    if getattr(runtime, "_isolated_coding_required", False) is not True:
        blockers.append("isolated_coding_mode_not_enabled")
    if not callable(getattr(runtime, "_isolated_coding_dispatch", None)):
        blockers.append("isolated_coding_dispatch_not_assembled")
    if not callable(getattr(runtime, "_isolated_coding_session_factory", None)):
        blockers.append("isolated_coding_session_not_assembled")
    if getattr(runtime, "_isolated_coding_image", None) != payload["coding_profile"]["image"]:
        blockers.append("isolated_coding_image_binding_not_assembled")
    return _report(blockers)


def _report(blockers):
    # Callables and compatible snapshots alone cannot prove OS isolation or
    # commissioned image behavior. Do not add an imported ready=True escape.
    return {"configuration_and_assembly_checks_passed": not blockers,
            "ready_to_execute": False, "write_authorized": False,
            "validation_authorized": False,
            "blockers": list(dict.fromkeys([
                *blockers, "isolated_coding_commissioning_not_established",
                "isolated_git_write_coordinator_not_assembled"]))}
