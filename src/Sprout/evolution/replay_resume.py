"""Explicit local CLI recovery of a pristine image-preflight wait, once only."""

import asyncio
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from Sprout.evolution.isolated_replay import content_digest, isolated_settings
from Sprout.evolution.replay_plan import verify_plan
from Sprout.evolution.replay_recovery import inspect_recovery
from Sprout.gateway.local_identity import creator_record, current_cli_principal
from Sprout.storage.local.sqlite.verification_report import recovery_facts


async def resume_replay(database: Path, task_id: str, *, settings, principal,
                        approval_id: str | None = None, source_metadata=None,
                        runtime_factory=None) -> dict:
    """Never resume source nodes, grant globally, release ownership, or retry unknown work."""
    from Sprout.runtime.factory import create_runtime

    def waiting(reason):
        return {"task_id": task_id, "status": "waiting_resource", "reason": reason,
                "validation_committed": False}

    if creator_record(principal) != creator_record(current_cli_principal()):
        return waiting("current_cli_identity_mismatch")
    if os.getenv("TEMPORAL_HOST") or settings.mcp.clients:
        return waiting("shared_or_external_runtime_not_supported")
    before = inspect_recovery(database, task_id, settings=settings, principal=principal)
    if before["blockers"] != ["current_runtime_snapshot_required"]:
        return waiting(before["blockers"])
    facts = recovery_facts(database, task_id)
    plan = verify_plan(facts["metadata"]["replay_plan"])
    directory = database.resolve().parent
    if facts["metadata"].get("regression_fingerprint"):
        from Sprout.evolution.regression_spec import frozen

        try:
            frozen(directory, facts["metadata"]["regression_fingerprint"])
        except (ValueError, OSError, TypeError):
            return waiting("frozen_regression_oracle_unavailable_or_changed")
    if database.resolve().name != "metadata.db":
        return waiting("isolated_metadata_path_required")
    if (directory / "independent-measurement.json").exists():
        return waiting("existing_measurement_evidence")
    candidate = None
    if plan["candidate"]:
        if source_metadata is None:
            return waiting("candidate_authority_required")
        snapshot = plan["candidate"]
        candidate = await source_metadata.get_artifact(snapshot["artifact_id"])
        if (candidate is None or candidate.version != snapshot["artifact_version"]
                or content_digest(candidate.content) != snapshot["content_hash"]):
            return waiting("candidate_snapshot_changed")
        from Sprout.artifacts.models import ArtifactKind
        from Sprout.evolution.candidate_evaluator import CandidateEvaluator
        from Sprout.evolution.candidate_manager import CandidateManager

        if candidate.kind not in {ArtifactKind.SKILL, ArtifactKind.WORKFLOW}:
            return waiting("candidate_kind_unsupported")
        report = CandidateEvaluator().evaluate(CandidateManager._to_candidate(candidate))
        if not report.passed and not report.pending:
            return waiting("candidate_static_gates_failed")
    isolated = isolated_settings(directory, settings)
    runtime = (runtime_factory or create_runtime)(isolated)
    runtime._isolated_coding_required = True
    owner = str(uuid4())
    claimed = False
    try:
        await runtime.start()
        if candidate is not None:
            from Sprout.skills.models import Skill, TrustLevel

            runtime.skills.register(Skill(name=candidate.name, version=candidate.version,
                                          instructions=candidate.content,
                                          trust=TrustLevel.TRUSTED.value))
        checked = inspect_recovery(database, task_id, settings=isolated,
                                   principal=principal, runtime=runtime)
        if checked["blockers"] or checked["plan_fingerprint"] != before["plan_fingerprint"]:
            return waiting(checked["blockers"] or "plan_changed_during_assembly")
        task = await runtime.get_task(task_id)
        if (task.actor != principal or task.source != "cli"
                or task.metadata["replay_plan"] != facts["metadata"]["replay_plan"]):
            return waiting("persisted_identity_or_plan_mismatch")
        if not callable(getattr(runtime, "_isolated_coding_dispatch", None)):
            return waiting("isolated_coding_backend_not_assembled")
        if approval_id:
            from Sprout.security.approval import ApprovalStatus

            grant = await runtime.approvals.store.get_approval(approval_id)
            if (grant is None or grant.task_id != task_id or grant.tool != "process_run"
                    or grant.source != "cli" or grant.requested_by != principal.user_id
                    or grant.status is not ApprovalStatus.PENDING):
                return waiting("approval_not_current_task_pending_request")
            # Explicit human CLI option; record only, never generic task/session resume.
            await runtime.approvals.decide(grant.id, True, decided_by=principal.user_id,
                                           channel="cli", reason="Explicit replay recovery")
        from Sprout.execution.docker_verification import DockerVerificationBackend, image_ready

        inspection = await DockerVerificationBackend().inspect_image(
            runtime, task_id, plan["verification_image"])
        if not image_ready(inspection):
            return {**waiting("image_unavailable_unapproved_or_incompatible"),
                    "approval_id": inspection.approval_id}
        if not callable(getattr(runtime, "_isolated_coding_dispatch", None)):
            return waiting("isolated_coding_backend_not_assembled")
        # Recheck after approval/daemon I/O; CAS rechecks database predicates atomically.
        checked = inspect_recovery(database, task_id, settings=isolated,
                                   principal=principal, runtime=runtime)
        if checked["blockers"] or checked["plan_fingerprint"] != before["plan_fingerprint"]:
            return waiting(checked["blockers"] or "plan_changed")
        claimed = await runtime.storage.metadata.claim_replay_resume({
            "task_id": task_id, "owner": owner, "requested_by": principal.user_id,
            "plan_fingerprint": before["plan_fingerprint"],
        })
        if not claimed:
            return waiting("resume_already_owned_or_state_changed")
        checked = inspect_recovery(database, task_id, settings=isolated,
                                   principal=principal, runtime=runtime)
        if (checked["blockers"] != ["existing_resume_ownership"]
                or checked["plan_fingerprint"] != before["plan_fingerprint"]):
            await runtime.storage.metadata.finish_replay_resume(
                task_id, owner, "uncertain", {"reason": "evidence_changed_after_claim"})
            return waiting("evidence_changed_after_claim")
        result = await runtime.execute(task, replay_resume_owner=owner)
        from Sprout.evolution.replay_docker import measure_docker
        from Sprout.evolution.replay_measurement import write_evidence
        from Sprout.evolution.replay_oracle import integrity

        repo = Path(plan["repository_root"])
        intact = integrity(repo, tuple(plan["oracle_paths"]), plan["oracle_snapshot"])
        records = []
        function_records = []
        regression_records = []
        if result.status == "completed" and intact:
            records = await measure_docker(
                runtime, task_id, plan["test_commands"], plan["regression_commands"],
                directory=directory, repo=repo, original=Path(plan["original_root"]),
                image=plan["verification_image"], oracle_paths=tuple(plan["oracle_paths"]),
                frozen=plan["oracle_snapshot"], classify_overrides=isolated.security.classify.rules,
            )
            if not any(r.get("waiting") for r in records):
                from Sprout.evolution.replay_functions import measure_functions

                function_records = await measure_functions(
                    runtime, task_id, directory=directory, repo=repo,
                    original=Path(plan["original_root"]), image=plan["verification_image"],
                    oracle_paths=tuple(plan["oracle_paths"]), frozen=plan["oracle_snapshot"],
                    acceptance_spec=task.metadata.get("acceptance_spec"),
                    classify_overrides=isolated.security.classify.rules,
                    regression_fingerprint=task.metadata.get("regression_fingerprint"),
                )
                if not any(r.get("waiting") for r in function_records):
                    regression_records = await measure_functions(
                        runtime, task_id, directory=directory, repo=repo,
                        original=Path(plan["original_root"]), image=plan["verification_image"],
                        oracle_paths=tuple(plan["oracle_paths"]), frozen=plan["oracle_snapshot"],
                        acceptance_spec=None, classify_overrides=isolated.security.classify.rules,
                        regression_fingerprint=task.metadata.get("regression_fingerprint"),
                        purpose="regression",
                    )
        measurement_status = ("waiting_resource" if any(
            r.get("waiting") for r in (*records, *function_records, *regression_records))
                              else "observed" if records else "not_measured")
        path = write_evidence(directory, {
            "schema_version": 1, "task_id": task_id,
            "source_task_id": plan["source_task_id"], "revision": plan["revision"],
            "candidate_id": candidate.id if candidate else None,
            "candidate_hash": plan["candidate"]["content_hash"] if candidate else None,
            "test_commands": plan["test_commands"],
            "regression_commands": plan["regression_commands"],
            "acceptance_spec": task.metadata.get("acceptance_spec"),
            "oracle_paths": plan["oracle_paths"], "oracle_snapshot": plan["oracle_snapshot"],
            "oracle_unchanged": integrity(
                repo, tuple(plan["oracle_paths"]), plan["oracle_snapshot"]),
            "task_status": result.status, "records": records,
            "function_records": function_records,
            "regression_records": regression_records,
            "regression_fingerprint": task.metadata.get("regression_fingerprint"),
            "measurement_status": measurement_status, "validation_committed": False,
        })
        output = {**asdict(result), "measurement_path": path,
                  "measurement_status": measurement_status, "validation_committed": False}
        if not await runtime.storage.metadata.finish_replay_resume(
            task_id, owner, "completed", output
        ):
            return waiting("resume_settlement_unconfirmed")
        return output
    except BaseException:
        if claimed:
            try:
                await asyncio.shield(runtime.storage.metadata.finish_replay_resume(
                    task_id, owner, "uncertain", {"reason": "resume_result_unconfirmed"}))
            except Exception:
                pass  # Durable active ownership remains non-retryable.
        raise
    finally:
        await runtime.stop()
