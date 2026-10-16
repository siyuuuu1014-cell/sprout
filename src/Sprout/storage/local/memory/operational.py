"""In-memory operational store for tests and ephemeral runtimes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

from Sprout.security.approval import ApprovalRecord, ApprovalStatus
from Sprout.session.models import Session, Turn
from Sprout.storage.contracts.commit_request import decision_matches, validate_request
from Sprout.storage.contracts.operational import Task


class MemoryOperationalStore:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.turns: list[Turn] = []
        self.approvals: dict[str, ApprovalRecord] = {}
        self.tasks: dict[str, Task] = {}
        self.commit_requests: dict[str, dict] = {}
        self.commit_dispatches: dict[str, dict] = {}

    async def get_commit_dispatch(self, request_id):
        return deepcopy(self.commit_dispatches.get(request_id))

    async def ensure_commit_dispatch(self, record):
        from Sprout.storage.contracts.commit_request import validate_dispatch

        validate_dispatch(record)
        current = self.commit_dispatches.get(record["request_id"])
        if current is None:
            request = self.commit_requests.get(record["request_id"])
            if (request is None or request["state"] != "approved"
                    or request["dispatch_state"] != "waiting_schedule"
                    or request["fingerprint"] != record["request_fingerprint"]
                    or request["decision"]["approved"] is not True
                    or request["decision"]["by"] != request["creator"]):
                raise ValueError("Approved request required")
            current = {**deepcopy(record), "state": "pending", "run_id": None}
            self.commit_dispatches[record["request_id"]] = current
        if any(current[key] != value for key, value in record.items()):
            raise ValueError("Immutable dispatch binding required")
        return deepcopy(current)

    async def mark_commit_dispatch(self, request_id, state, run_id=None):
        from Sprout.storage.contracts.commit_request import validate_dispatch_state

        validate_dispatch_state(state, run_id)
        record = self.commit_dispatches.get(request_id)
        if record is None:
            return False
        if record["state"] in {"pending", "uncertain"}:
            record.update(state=state, run_id=run_id)
            self.commit_requests[request_id]["dispatch_state"] = state
        return record["state"] == state and record["run_id"] == run_id

    async def save_commit_request(self, record):
        validate_request(record)
        current = self.commit_requests.get(record["id"])
        if current is not None and (current.get("supersedes") != record.get("supersedes")
                or any(current[key] != record[key] for key in
                ("id", "task_id", "proposal_id", "creator", "review", "fingerprint"))):
            raise ValueError("Commit request ID already bound to different content")
        if current is None:
            self.commit_requests[record["id"]] = {**deepcopy(record), "state": "pending",
                                                "decision": None, "dispatch_state": None}
        return deepcopy(self.commit_requests[record["id"]])

    async def get_commit_request(self, request_id):
        return deepcopy(self.commit_requests.get(request_id))

    async def decide_commit_request(self, request_id, *, creator, expected_fingerprint,
                                    approved, reason):
        if type(approved) is not bool or not isinstance(reason, str) or len(reason) > 1024:
            raise ValueError("Explicit bounded decision required")
        record = self.commit_requests.get(request_id)
        now = datetime.now(UTC)
        if (record is None or record["state"] != "pending" or not decision_matches(record,
                creator=creator, expected_fingerprint=expected_fingerprint, now=now)):
            return False
        record.update(state="approved" if approved else "rejected",
            decision={"approved": approved, "by": creator, "at": now.isoformat(), "reason": reason},
            dispatch_state="waiting_schedule" if approved else None)
        return True

    # Sessions and turns
    async def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    async def save_session(self, session: Session) -> None:
        self.sessions[session.id] = session

    async def append_turn(self, turn: Turn) -> None:
        self.turns.append(turn)

    async def recent_turns(self, session_id: str, limit: int = 20) -> list[Turn]:
        return [turn for turn in self.turns if turn.session_id == session_id][-limit:]

    async def count_sessions(self) -> int:
        return len(self.sessions)

    async def count_turns(self) -> int:
        return len(self.turns)

    # Approvals
    async def save_approval(self, record: ApprovalRecord) -> None:
        self.approvals[record.id] = replace(record)

    async def save_approval_if_status(
        self, record: ApprovalRecord, expected: ApprovalStatus
    ) -> bool:
        current = self.approvals.get(record.id)
        if current is None or current.status is not expected:
            return False
        self.approvals[record.id] = replace(record)
        return True

    async def consume_approval_exact(self, record: ApprovalRecord, used_at) -> bool:
        current = self.approvals.get(record.id)
        if (current != record or record.status is not ApprovalStatus.APPROVED
                or record.single_use is not True or record.used_at is not None
                or record.expires_at is None or record.expires_at <= used_at):
            return False
        self.approvals[record.id] = replace(record, status=ApprovalStatus.CONSUMED, used_at=used_at)
        return True

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        record = self.approvals.get(approval_id)
        return replace(record) if record is not None else None

    async def list_approvals(
        self, status: ApprovalStatus | None = None
    ) -> list[ApprovalRecord]:
        return [
            replace(record)
            for record in self.approvals.values()
            if status is None or record.status is status
        ]

    async def find_approval(
        self,
        tool: str,
        arguments_fingerprint: str,
        status: ApprovalStatus,
        task_id: str = "",
        class_key: str = "",
    ) -> ApprovalRecord | None:
        exact = None
        for record in self.approvals.values():
            if (
                record.tool == tool
                and record.arguments_fingerprint == arguments_fingerprint
                and record.status is status
                and record.task_id == task_id
            ):
                exact = replace(record)
                break
        if exact is not None:
            return exact
        return await self.find_class_approval(tool, class_key, status, task_id)

    async def find_class_approval(
        self,
        tool: str,
        class_key: str,
        status: ApprovalStatus,
        task_id: str = "",
    ) -> ApprovalRecord | None:
        """A reusable grant covering a whole command name, not one invocation."""
        if not class_key:
            return None
        for record in self.approvals.values():
            if (
                record.tool == tool
                and record.class_key == class_key
                and not record.single_use
                and record.status is status
                and record.task_id == task_id
            ):
                return replace(record)
        return None

    async def find_reusable_approval(
        self,
        tool: str,
        approval_class: str,
        status: ApprovalStatus,
        *,
        session_id: str,
        requested_by: str,
        source: str,
        task_id: str = "",
        resource_scope: str = "",
    ) -> ApprovalRecord | None:
        for record in self.approvals.values():
            same_boundary = (
                record.session_id == session_id
                if session_id
                else record.task_id == task_id
            )
            if (
                record.tool == tool
                and record.approval_class == approval_class
                and record.status is status
                and not record.single_use
                and same_boundary
                and record.requested_by == requested_by
                and record.source == source
                and record.resource_scope == resource_scope
            ):
                return replace(record)
        return None

    # Tasks
    async def save_task(self, task: Task) -> None:
        self.tasks[task.id] = task

    async def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def list_tasks(self, status: str | None = None) -> list[Task]:
        return [task for task in self.tasks.values() if status is None or task.status == status]
