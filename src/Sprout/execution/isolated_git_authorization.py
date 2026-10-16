"""Exact experiment Git approval binding; inspection never grants write authority."""

from datetime import UTC, datetime

from Sprout.execution.git_transaction_contract import fingerprint, validate_commit_write_contract
from Sprout.gateway.local_identity import creator_blockers
from Sprout.security.approval import ApprovalManager, ApprovalStatus


def git_approval_arguments(*, task_id, proposal_id, operation, replay_fingerprint,
                           git_plan, proposal_fingerprint, verification_fingerprint,
                           write_contract=None):
    if operation not in {"commit", "rollback"} or git_plan.get("operation") != operation:
        raise ValueError("Exact experiment Git operation required")
    if git_plan.get("write_authorized") is not False:
        raise ValueError("Read-only preflight plan required")
    values = {"task_id": task_id, "proposal_id": proposal_id, "operation": operation,
              "replay_fingerprint": replay_fingerprint, "target": git_plan.get("target"),
              "git_plan_fingerprint": fingerprint(git_plan),
              "proposal_fingerprint": proposal_fingerprint,
              "verification_fingerprint": verification_fingerprint}
    if not all(isinstance(value, str) and value for value in values.values()):
        raise ValueError("Complete experiment approval binding required")
    if write_contract is not None:
        if operation != "commit":
            raise ValueError("Commit write contract cannot authorize rollback")
        validate_commit_write_contract(write_contract, git_plan)
        return {"schema_version": 2, "boundary": "isolated_git", **values,
                "write_contract_fingerprint": fingerprint(write_contract)}
    return {"schema_version": 1, "boundary": "isolated_git", **values}


def git_approval_blockers(record, arguments, *, creator, principal, now=None):
    """No reusable-class fallback, grant consumption, or trusted-measurement inference."""
    blockers = creator_blockers(creator, principal)
    expected_fields = {"schema_version", "boundary", "task_id", "proposal_id", "operation",
                       "replay_fingerprint", "target", "git_plan_fingerprint",
                       "proposal_fingerprint", "verification_fingerprint"}
    if isinstance(arguments, dict) and arguments.get("schema_version") == 2:
        expected_fields.add("write_contract_fingerprint")
    if (not isinstance(arguments, dict) or set(arguments) != expected_fields
            or type(arguments.get("schema_version")) is not int
            or arguments["schema_version"] not in {1, 2}
            or arguments.get("boundary") != "isolated_git"
            or arguments.get("operation") not in {"commit", "rollback"}
            or (arguments.get("schema_version") == 2 and arguments.get("operation") != "commit")
            or any(not isinstance(arguments[key], str) or not arguments[key]
                   for key in expected_fields - {"schema_version"})):
        return [*blockers, "exact_git_approval_arguments_invalid"]
    if record is None:
        return [*blockers, "exact_git_approval_missing"]
    if record.status is not ApprovalStatus.APPROVED or record.used_at is not None:
        blockers.append("exact_git_approval_not_usable")
    if record.expires_at is None or record.expires_at <= (now or datetime.now(UTC)):
        blockers.append("exact_git_approval_expired_or_unbounded")
    if record.tool != "git.commit" or record.single_use is not True:
        blockers.append("exact_git_approval_action_or_usage_mismatch")
    if (record.task_id != arguments["task_id"]
            or record.resource_scope != arguments["proposal_id"]):
        blockers.append("exact_git_approval_scope_mismatch")
    action_hash = ApprovalManager.fingerprint({"tool": "git.commit", **arguments})
    if (record.arguments_fingerprint != ApprovalManager.fingerprint(arguments)
            or record.action_hash != action_hash):
        blockers.append("exact_git_approval_fingerprint_mismatch")
    creator_id = creator.get("user_id") if isinstance(creator, dict) else None
    if (not creator_id or record.requested_by != creator_id or record.decided_by != creator_id
            or record.source != "cli" or record.decided_at is None):
        blockers.append("exact_git_approval_creator_mismatch")
    return list(dict.fromkeys(blockers))


def commit_approval_blockers(record, arguments, *, git_plan, write_contract, creator, principal):
    """A future commit coordinator must reject legacy grants without write options."""
    blockers = git_approval_blockers(record, arguments, creator=creator, principal=principal)
    try:
        validate_commit_write_contract(write_contract, git_plan)
        if (arguments.get("schema_version") != 2 or arguments.get("operation") != "commit"
                or arguments.get("target") != git_plan["target"]
                or arguments.get("git_plan_fingerprint") != fingerprint(git_plan)
                or arguments.get("write_contract_fingerprint") != fingerprint(write_contract)):
            blockers.append("exact_commit_write_contract_approval_required")
    except (ValueError, TypeError, KeyError, AttributeError):
        blockers.append("commit_write_contract_invalid")
    return list(dict.fromkeys(blockers))


async def consume_commit_approval(operational, *, approval_id, arguments, git_plan,
                                  write_contract, creator, principal):
    """Internal one-shot consumption; not measurement, claim or Git write authority.

    Call only after durable write ownership and evidence checks. Failure or
    interruption never releases that ownership or permits automatic retry.
    """
    record = await operational.get_approval(approval_id)
    blockers = commit_approval_blockers(record, arguments, git_plan=git_plan,
        write_contract=write_contract, creator=creator, principal=principal)
    if blockers:
        return {"consumed": False, "blockers": blockers}
    consumed = await operational.consume_approval_exact(record, datetime.now(UTC))
    return {"consumed": consumed,
            "blockers": [] if consumed else ["exact_git_approval_changed_or_consumed"]}


async def inspect_git_approval(operational, *, approval_id, arguments, creator, principal):
    """Read persisted evidence, never accept an imported approved flag or issue a grant.

    A passing approval check remains insufficient: the source-bound measurement
    handoff and consuming/claiming write coordinator are not assembled yet.
    """
    record = await operational.get_approval(approval_id)
    blockers = git_approval_blockers(record, arguments, creator=creator, principal=principal)
    return {"approval_id": record.id if record is not None else None,
            "approval_binding_passed": not blockers, "write_authorized": False,
            "blockers": [*blockers, "isolated_git_measurement_handoff_not_established",
                         "isolated_git_write_coordinator_not_assembled"]}
