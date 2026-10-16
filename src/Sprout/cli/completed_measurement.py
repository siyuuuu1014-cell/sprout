"""Explicit independent measurement after a confirmed isolated commit."""

import json
from pathlib import Path

from Sprout.cli.commit_submission import _route
from Sprout.cli.experiment_runtime import experiment_runtime
from Sprout.evolution.coding_assembly import build_coding_session_factory
from Sprout.evolution.isolated_replay import git
from Sprout.evolution.replay_measurement import COMPLETED_MEASUREMENT
from Sprout.evolution.replay_plan import verify_plan
from Sprout.storage.local.sqlite.commit_status import _regular, commit_status_report
from Sprout.storage.local.sqlite.verification_report import recovery_facts


def _check_repository(plan, completion):
    repo = Path(plan["repository_root"])
    if (git(repo, "rev-parse", "HEAD") != completion["result_commit"]
            or git(repo, "status", "--porcelain")):
        raise ValueError("Experiment repository must match its clean completed commit")
    return repo


async def measure_completed(database, task_id, *, settings, principal, approval_id=None):
    """No task execution, Git write, report replacement or candidate publication."""
    from Sprout.evolution.comparison_report import _read
    from Sprout.evolution.replay_docker import measure_docker
    from Sprout.evolution.replay_functions import measure_functions
    from Sprout.evolution.replay_oracle import integrity

    database = _regular(database)
    _route(database, task_id, principal)
    facts = recovery_facts(database, task_id)
    completion = facts["metadata"].get("isolated_commit_completion", {})
    request_id = completion.get("request_id")
    if facts["status"] != "completed" or not request_id:
        raise ValueError("Confirmed completed isolated commit required before measurement")
    report = commit_status_report(database, task_id, principal=principal, request_id=request_id)
    if not report["requests"][0]["task_completed"] or report["requests"][0]["status"] != "applied":
        raise ValueError("Trusted applied commit and task completion required")
    async with experiment_runtime(database, task_id, settings=settings) as (runtime, task):
        if (task.status != "completed"
                or task.metadata.get("isolated_commit_completion") != completion):
            raise ValueError("Task completion changed during assembly")
        build_coding_session_factory(runtime, task)  # Validate bindings only; install no callbacks.
        plan = verify_plan(task.metadata["replay_plan"])
        repo = _check_repository(plan, completion)
        directory = database.parent
        destination = directory / COMPLETED_MEASUREMENT
        binding = {"task_id": task_id, "result_commit": completion["result_commit"],
                   "plan_fingerprint": task.metadata["replay_plan"]["fingerprint"]}
        if destination.exists():
            cached = _read(destination)
            if any(cached.get(key) != value for key, value in binding.items()):
                raise ValueError("Existing completed measurement binding mismatch")
            return {**binding, "measurement_path": str(destination),
                    "measurement_status": cached["measurement_status"], "cached": True,
                    "validation_committed": False}
        if approval_id is not None:
            from Sprout.security.approval import ApprovalStatus

            grant = await runtime.approvals.store.get_approval(approval_id)
            if (grant is None or grant.task_id != task_id or grant.source != "cli"
                    or grant.requested_by != principal.user_id or grant.tool != "process_run"
                    or grant.resource_scope != task.workspace_id
                    or grant.status is not ApprovalStatus.PENDING):
                raise ValueError("Exact current-task pending process approval required")
            decision = await runtime.approvals.decide(approval_id, True,
                decided_by=principal.user_id, channel="cli",
                reason="Explicit replay-measure process approval; no code/Git approval")
            if decision.status is not ApprovalStatus.APPROVED:
                return {**binding, "measurement_status": "waiting_resource",
                        "measurement_path": None, "reason": "approval_not_granted",
                        "validation_committed": False}
        options = dict(directory=directory, repo=repo, original=Path(plan["original_root"]),
                       image=plan["verification_image"], oracle_paths=tuple(plan["oracle_paths"]),
                       frozen=plan["oracle_snapshot"],
                       classify_overrides=runtime._settings.security.classify.rules)
        if not integrity(repo, options["oracle_paths"], options["frozen"]):
            raise ValueError("Frozen oracle changed")
        records = await measure_docker(runtime, task_id,
            plan["test_commands"], plan["regression_commands"], **options)
        functions, regressions = [], []
        if not any(item.get("waiting") for item in records):
            functions = await measure_functions(runtime, task_id, **options,
                acceptance_spec=task.metadata.get("acceptance_spec"),
                regression_fingerprint=task.metadata.get("regression_fingerprint"))
            if not any(item.get("waiting") for item in functions):
                regressions = await measure_functions(runtime, task_id, **options,
                    acceptance_spec=None, purpose="regression",
                    regression_fingerprint=task.metadata.get("regression_fingerprint"))
        if any(item.get("waiting") for item in (*records, *functions, *regressions)):
            return {**binding, "measurement_status": "waiting_resource",
                    "measurement_path": None, "validation_committed": False,
                    "blockers": [item.get("reason", "measurement_waiting")
                                 for item in (*records, *functions, *regressions)
                                 if item.get("waiting")],
                    "pending_approvals": [{"approval_id": grant.id, "tool": grant.tool,
                        "action_summary": grant.action_summary}
                        for grant in await runtime.pending_approvals() if grant.task_id == task_id],
                    "next_action": "Review persisted measurement evidence and process approvals"}
        _check_repository(plan, completion)
        current = await runtime.storage.metadata.get_task(task_id)
        if (current.status != "completed"
                or current.metadata.get("isolated_commit_completion") != completion
                or not integrity(repo, options["oracle_paths"], options["frozen"])):
            raise ValueError("Completion or oracle changed during measurement")
        payload = {"schema_version": 1, **binding, "task_status": "completed",
            "source_task_id": plan["source_task_id"], "revision": plan["revision"],
            "candidate_id": plan["candidate"]["artifact_id"] if plan["candidate"] else None,
            "candidate_hash": plan["candidate"]["content_hash"] if plan["candidate"] else None,
            "test_commands": plan["test_commands"],
            "regression_commands": plan["regression_commands"],
            "acceptance_spec": task.metadata.get("acceptance_spec"),
            "regression_fingerprint": task.metadata.get("regression_fingerprint"),
            "oracle_paths": plan["oracle_paths"], "oracle_snapshot": plan["oracle_snapshot"],
            "oracle_unchanged": True, "records": records, "function_records": functions,
            "regression_records": regressions, "measurement_status": "observed",
            "validation_committed": False}
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
        return {**binding, "measurement_path": str(destination),
                "measurement_status": "observed", "cached": False, "validation_committed": False}
