"""Non-mutating eligibility checks for image-preflight waits, not execution."""

from pathlib import Path

from Sprout.evolution.isolated_replay import git
from Sprout.evolution.replay_oracle import integrity
from Sprout.evolution.replay_plan import verify_plan
from Sprout.storage.local.sqlite.verification_report import recovery_facts


def inspect_recovery(database: Path, task_id: str, *, settings=None, runtime=None,
                     principal=None) -> dict:
    facts = recovery_facts(database, task_id)
    blockers = []
    fingerprint = None
    try:
        plan = verify_plan(facts["metadata"].get("replay_plan"))
        fingerprint = facts["metadata"]["replay_plan"]["fingerprint"]
        from Sprout.gateway.local_identity import creator_blockers

        blockers.extend(creator_blockers(plan.get("creator"), principal))
        from Sprout.evolution.replay_config import configuration_blockers

        if settings is None:
            blockers.append("current_configuration_required")
        else:
            blockers.extend(configuration_blockers(plan.get("configuration"), settings))
        from Sprout.evolution.replay_snapshot import snapshot_blockers

        blockers.extend(snapshot_blockers(plan.get("runtime_snapshot"), runtime))
        repo = Path(plan["repository_root"])
        directory = database.resolve().parent
        if repo != directory / "repo" or repo.is_symlink() or not repo.is_dir():
            blockers.append("isolated_repository_mismatch")
        elif Path(facts["workspace_root"]).resolve() != repo:
            blockers.append("workspace_binding_mismatch")
        else:
            original = Path(plan["original_root"])
            if repo.is_relative_to(original) or original.is_relative_to(repo):
                blockers.append("source_repository_overlap")
            if git(repo, "rev-parse", "HEAD") != plan["revision"]:
                blockers.append("revision_changed")
            if git(repo, "status", "--porcelain", "--untracked-files=all"):
                blockers.append("repository_changed")
            if not integrity(repo, tuple(plan["oracle_paths"]), plan["oracle_snapshot"]):
                blockers.append("frozen_oracle_changed")
        if plan["execution_mode"] != "code":
            blockers.append("not_code_preflight")
        if facts["metadata"].get("execution_mode") != plan["execution_mode"]:
            blockers.append("task_mode_mismatch")
        if facts["metadata"].get("replay_source_task_id") != plan["source_task_id"]:
            blockers.append("source_task_binding_mismatch")
    except Exception:
        # No mutation or fallback if evidence/Git cannot be inspected.
        blockers.append("plan_or_repository_unavailable")
    if facts["status"] != "waiting_resource":
        blockers.append("task_not_waiting_resource")
    if facts["metadata"].get("replay_stage") != "image_preflight":
        blockers.append("not_image_preflight_wait")
    if facts["node_count"]:
        blockers.append("existing_execution_nodes")
    if facts["claim_count"]:
        blockers.append("existing_verification_claims")
    if facts.get("resume_count"):
        blockers.append("existing_resume_ownership")
    return {
        "task_id": task_id, "plan_fingerprint": fingerprint,
        "preflight_checks_passed": not blockers, "blockers": blockers,
        "read_only": True, "resume_authorized": False,
        "pending_checks": ["current_identity_and_policy", "image_approval_and_readiness",
                           "candidate_snapshot_registration", "atomic_resume_ownership"],
    }
