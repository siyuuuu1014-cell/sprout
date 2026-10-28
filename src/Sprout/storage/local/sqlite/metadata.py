"""SQLite implementation of the SEMA MetadataStore."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from Sprout.artifacts.models import Artifact, ArtifactKind, ArtifactStatus
from Sprout.execution.models import (
    ChangeProposal,
    ChangeProposalStatus,
    DiffResult,
    SandboxRef,
    TestResult,
)
from Sprout.gateway.identity import Principal
from Sprout.orchestration.models import (
    DataRef,
    ExecutionNode,
    NodeBudget,
    NodeStatus,
    NodeType,
)
from Sprout.orchestration.temporal_codec import node_to_dict
from Sprout.storage.local.sqlite.driver import SqliteDatabase, SqlitePragmas
from Sprout.storage.local.sqlite.git_transaction import (
    SCHEMA as GIT_TRANSACTION_SCHEMA,
)
from Sprout.storage.local.sqlite.git_transaction import (
    claim_transaction,
    finish_transaction,
    get_transaction,
    proposal_snapshot,
)
from Sprout.storage.local.sqlite.model_budget import SCHEMA as MODEL_BUDGET_SCHEMA
from Sprout.task.models import (
    DelegationScope,
    Task,
    TaskBudget,
    TaskStatus,
)
from Sprout.trajectory.models import ArtifactSnapshot, Trajectory, TrajectoryStatus
from Sprout.workspace.models import (
    Workspace,
    WorkspaceKind,
    WorkspaceManifest,
)

SCHEMA = MODEL_BUDGET_SCHEMA + """
CREATE TABLE IF NOT EXISTS node_result_sources_v1 (
    node_id TEXT PRIMARY KEY,
    source_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coding_intents (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coding_executions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    state TEXT NOT NULL,
    record_json TEXT NOT NULL,
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS replay_resumes (
    task_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    state TEXT NOT NULL,
    record_json TEXT NOT NULL,
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS verification_executions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    state TEXT NOT NULL,
    record_json TEXT NOT NULL,
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS task_submissions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_task ON task_submissions(task_id);
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    root TEXT NOT NULL,
    kind TEXT NOT NULL,
    revision TEXT,
    manifest_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    instruction TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    delegation_scope_json TEXT NOT NULL,
    budget_json TEXT NOT NULL,
    phases_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks(workspace_id, created_at);

CREATE TABLE IF NOT EXISTS execution_nodes (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    type TEXT NOT NULL,
    dependencies_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    budget_json TEXT NOT NULL,
    result_ref_json TEXT,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_task ON execution_nodes(task_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    scope TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_name ON artifacts(name, version);

CREATE TABLE IF NOT EXISTS trajectories (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    workspace_revision TEXT,
    status TEXT NOT NULL,
    artifact_snapshots_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trajectories_task ON trajectories(task_id, created_at);

CREATE TABLE IF NOT EXISTS change_proposals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    sandbox_json TEXT,
    files_changed_json TEXT NOT NULL,
    test_results_json TEXT NOT NULL,
    diffs_json TEXT NOT NULL,
    risk TEXT NOT NULL,
    required_capability_diff_json TEXT NOT NULL,
    rollback_plan TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_change_proposals_task ON change_proposals(task_id, created_at);
"""


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class SqliteMetadataStore:
    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db
        db.executescript(SCHEMA)
        db.executescript(GIT_TRANSACTION_SCHEMA)

    def close(self) -> None:
        self._db.close()

    async def get_git_proposal_snapshot(self, proposal_id):
        return await proposal_snapshot(self._db, proposal_id)

    async def get_git_transaction(self, transaction_id):
        return await get_transaction(self._db, transaction_id)

    async def claim_git_transaction(self, record, snapshot):
        return await claim_transaction(self._db, record, snapshot)

    async def finish_git_transaction(self, transaction_id, owner, state, result):
        return await finish_transaction(self._db, transaction_id, owner, state, result)

    async def begin_git_commit_write(self, transaction_id, owner):
        from Sprout.storage.local.sqlite.git_transaction import begin_commit_write

        return await begin_commit_write(self._db, transaction_id, owner)

    async def activate_commit_task(self, task_id, replay_fingerprint):
        """Explicit persisted commit decision may continue only its approval wait.

        Never revive paused, failed, cancelled or completed tasks. Coordinator
        admission and live node/transaction CAS remain mandatory afterwards.
        """
        changed = await self._db.execute("UPDATE tasks SET status='running' "
            "WHERE id=? AND status IN ('running','waiting_approval') "
            "AND json_extract(metadata_json,'$.execution_mode')='code' "
            "AND json_extract(metadata_json,'$.replay_plan.payload.schema_version')=5 "
            "AND json_extract(metadata_json,'$.replay_plan.fingerprint')=?",
            (task_id, replay_fingerprint))
        return changed == 1

    async def complete_isolated_commit_task(
        self, task_id, request_id, request_fingerprint, evaluation_node_id,
    ):
        from Sprout.storage.local.sqlite.commit_completion import complete_commit_task

        return await complete_commit_task(self._db, task_id, request_id,
                                          request_fingerprint, evaluation_node_id)

    async def settle_git_commit_proposal(self, transaction_id, owner, result):
        from Sprout.storage.local.sqlite.git_transaction import settle_commit_proposal

        return await settle_commit_proposal(self._db, transaction_id, owner, result)

    async def get_replay_resume(self, task_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM replay_resumes WHERE task_id=?", (task_id,))
        if row is None:
            return None
        return {**_json_loads(row["record_json"], {}), "state": row["state"],
                "result": _json_loads(row["result_json"], None)}

    async def claim_replay_resume(self, record: dict[str, Any]) -> bool:
        """One non-expiring owner; this claim is not an approval grant."""
        if not record.get("owner") or not record.get("plan_fingerprint"):
            raise ValueError("Resume owner and plan fingerprint are required")
        changed = await self._db.execute(
            "INSERT OR IGNORE INTO replay_resumes "
            "(task_id, owner, state, record_json) SELECT ?, ?, 'active', ? "
            "WHERE EXISTS (SELECT 1 FROM tasks WHERE id=? AND status='waiting_resource' "
            "AND json_extract(metadata_json, '$.execution_mode')='code' "
            "AND json_extract(metadata_json, '$.replay_stage')='image_preflight' "
            "AND json_extract(metadata_json, '$.replay_plan.fingerprint')=? "
            "AND json_extract(metadata_json, '$.replay_plan.payload.schema_version') IN (4,5) "
            "AND json_extract(metadata_json, '$.replay_plan.payload.creator.source')='cli' "
            "AND json_extract(metadata_json, '$.replay_plan.payload.creator.authenticated')=1 "
            "AND json_extract(metadata_json, '$.replay_plan.payload.creator.user_id')=? "
            "AND json_extract(metadata_json, "
            "'$.replay_plan.payload.configuration.schema_version')=1 "
            "AND json_extract(metadata_json, "
            "'$.replay_plan.payload.runtime_snapshot.schema_version')=2) "
            "AND NOT EXISTS (SELECT 1 FROM execution_nodes WHERE task_id=?) "
            "AND NOT EXISTS (SELECT 1 FROM verification_executions WHERE task_id=?)",
            (record["task_id"], record["owner"], _json_dumps(record), record["task_id"],
             record["plan_fingerprint"], record.get("requested_by"),
             record["task_id"], record["task_id"]),
        )
        return changed == 1

    async def finish_replay_resume(
        self, task_id: str, owner: str, state: str, result: dict[str, Any]
    ) -> bool:
        if state not in {"completed", "uncertain"}:
            raise ValueError("Invalid resume settlement state")
        changed = await self._db.execute(
            "UPDATE replay_resumes SET state=?, result_json=? "
            "WHERE task_id=? AND owner=? AND state='active'",
            (state, _json_dumps(result), task_id, owner),
        )
        return changed == 1

    async def get_verification_execution(self, execution_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM verification_executions WHERE id = ?", (execution_id,)
        )
        if row is None:
            return None
        return {
            **_json_loads(row["record_json"], {}),
            "state": row["state"],
            "result": _json_loads(row["result_json"], None),
        }

    async def get_coding_execution(self, execution_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM coding_executions WHERE id=?", (execution_id,))
        if row is None:
            return None
        return {**_json_loads(row["record_json"], {}), "state": row["state"],
                "result": _json_loads(row["result_json"], None)}

    async def get_coding_intent(self, intent_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT record_json FROM coding_intents WHERE id=?", (intent_id,))
        return _json_loads(row["record_json"], {}) if row is not None else None

    async def ensure_coding_intent(self, record: dict[str, Any]) -> dict[str, Any]:
        """Freeze host-produced inputs; never replace or add a slot after unknown effects."""
        await self._db.execute(
            "INSERT OR IGNORE INTO coding_intents SELECT ?, ?, ?, ? "
            "WHERE EXISTS (SELECT 1 FROM execution_nodes n JOIN tasks t ON t.id=n.task_id "
            "WHERE n.id=? AND n.task_id=? AND n.status='running' AND t.status='running' "
            "AND json_extract(n.metadata_json, '$.execution_guard.state')='active' "
            "AND json_extract(n.metadata_json, '$.execution_guard.owner')=? "
            "AND json_extract(t.metadata_json, '$.execution_mode')='code') "
            "AND NOT EXISTS (SELECT 1 FROM coding_executions WHERE node_id=? "
            "AND state IN ('active', 'uncertain'))",
            (record["id"], record["node_id"], record["signature"], _json_dumps(record),
             record["node_id"], record["task_id"], record["owner"], record["node_id"]))
        row = await self._db.fetchone(
            "SELECT i.record_json FROM coding_intents i JOIN execution_nodes n ON n.id=i.node_id "
            "JOIN tasks t ON t.id=n.task_id WHERE i.id=? AND n.task_id=? "
            "AND n.status='running' AND t.status='running' "
            "AND json_extract(n.metadata_json, '$.execution_guard.owner')=? "
            "AND json_extract(n.metadata_json, '$.execution_guard.state')='active' "
            "AND json_extract(t.metadata_json, '$.execution_mode')='code'",
            (record["id"], record["task_id"], record["owner"]))
        if row is None:
            raise ValueError("Coding intent authority unavailable or unknown effects exist")
        frozen = _json_loads(row["record_json"], {})
        if ({key: value for key, value in frozen.items() if key != "owner"}
                != {key: value for key, value in record.items() if key != "owner"}):
            raise ValueError("Coding intent changed; cannot replace frozen inputs")
        return frozen

    async def claim_coding_execution(self, record: dict[str, Any]) -> bool:
        if not record.get("owner") or not record.get("signature"):
            raise ValueError("Coding owner and signature required")
        changed = await self._db.execute(
            "INSERT INTO coding_executions "
            "(id, task_id, node_id, owner, state, record_json) SELECT ?, ?, ?, ?, 'active', ? "
            "WHERE EXISTS (SELECT 1 FROM execution_nodes n JOIN tasks t ON t.id=n.task_id "
            "WHERE n.id=? AND n.task_id=? AND n.status='running' AND t.status='running' "
            "AND json_extract(n.metadata_json, '$.execution_guard.state')='active' "
            "AND json_extract(n.metadata_json, '$.execution_guard.owner')=? "
            "AND json_extract(t.metadata_json, '$.execution_mode')='code') "
            "AND EXISTS (SELECT 1 FROM coding_intents WHERE id=? AND fingerprint=? AND node_id=?) "
            "AND NOT EXISTS (SELECT 1 FROM coding_executions WHERE node_id=? "
            "AND state IN ('active', 'uncertain')) "
            "ON CONFLICT(id) DO UPDATE SET owner=excluded.owner, state='active', "
            "record_json=json_set(excluded.record_json, '$.no_launch_history', "
            "json_insert(COALESCE(json_extract(coding_executions.record_json, "
            "'$.no_launch_history'), '[]'), '$[#]', json_object('owner', coding_executions.owner, "
            "'result', json(coding_executions.result_json)))), result_json=NULL "
            "WHERE coding_executions.state='not_started' "
            "AND coding_executions.node_id=excluded.node_id "
            "AND coding_executions.task_id=excluded.task_id "
            "AND json_extract(coding_executions.record_json, '$.signature') "
            "=json_extract(excluded.record_json, '$.signature')",
            (record["id"], record["task_id"], record["node_id"], record["owner"],
             _json_dumps(record), record["node_id"], record["task_id"], record["owner"],
             record["id"], record["signature"], record["node_id"], record["node_id"]),
        )
        return changed == 1

    async def finish_coding_execution(self, execution_id, owner, state, result) -> bool:
        if state not in {"completed", "uncertain", "not_started"}:
            raise ValueError("Coding settlement must be completed or uncertain")
        if state == "not_started" and (
            result.get("started") is not False or result.get("allowed") is not False
            or result.get("exit_code") is not None
        ):
            raise ValueError("Trusted broker no-launch evidence required")
        changed = await self._db.execute(
            "UPDATE coding_executions SET state=?, result_json=? "
            "WHERE id=? AND owner=? AND state='active'",
            (state, _json_dumps(result), execution_id, owner))
        return changed == 1

    async def claim_verification_execution(self, record: dict[str, Any]) -> bool:
        if record.get("schema_version") == 3:
            authority = record.get("authority", {})
            if (type(record.get("schema_version")) is not int
                    or any(not isinstance(record.get(key), str) or not record[key]
                           for key in ("id", "task_id", "node_id", "owner", "workspace_id"))
                    or not isinstance(authority, dict)
                    or set(authority) != {"actor", "scope", "task_metadata", "node_metadata",
                                          "workspace_root", "workspace_revision", "instruction"}):
                return False
            if (any(type(authority[k]) is not dict for k in
                    ("actor", "scope", "task_metadata", "node_metadata"))
                    or type(authority["workspace_root"]) is not str
                    or type(authority["instruction"]) is not str
                    or authority["workspace_revision"] is not None
                    and type(authority["workspace_revision"]) is not str):
                return False
            # One writer statement checks the actual Task and persisted Guard
            # and inserts once. No pre-query grants, state transition or schema migration.
            changed = await self._db.execute(
                "INSERT OR IGNORE INTO verification_executions "
                "(id, task_id, owner, state, record_json) "
                "SELECT ?, ?, ?, 'active', ? FROM tasks t JOIN execution_nodes n "
                "ON n.task_id=t.id JOIN workspaces w ON w.id=t.workspace_id "
                "WHERE t.id=? AND t.workspace_id=? AND t.status='running' "
                "AND w.root=? AND w.revision IS ? AND t.instruction=? "
                "AND json_extract(t.metadata_json,'$.execution_mode')='code' "
                "AND json_extract(t.actor_json,'$.authenticated')=1 "
                "AND json_type(t.actor_json,'$.authenticated')='true' "
                "AND t.source=json_extract(t.actor_json,'$.source') "
                "AND json_extract(t.actor_json,'$.user_id')!='' "
                "AND t.source!='' AND json(t.actor_json)=json(?) "
                "AND json(t.delegation_scope_json)=json(?) "
                "AND json(t.metadata_json)=json(?) "
                "AND (json_extract(t.delegation_scope_json,'$.expires_at') IS NULL "
                "OR ((json_extract(t.delegation_scope_json,'$.expires_at') LIKE '%Z' "
                "OR json_extract(t.delegation_scope_json,'$.expires_at') GLOB '*[+-]??:??') "
                "AND julianday(json_extract(t.delegation_scope_json,'$.expires_at')) "
                "> julianday('now'))) "
                "AND n.id=? AND n.type='evaluation' AND n.status='running' "
                "AND json(n.metadata_json)=json(?) "
                "AND json_extract(n.metadata_json,'$.execution_guard.state')='active' "
                "AND json_extract(n.metadata_json,'$.execution_guard.owner')=? "
                "AND (julianday('now')-julianday(json_extract(n.metadata_json, "
                "'$.execution_guard.started_at')))*86400 "
                "< json_extract(n.budget_json,'$.timeout_seconds')",
                (record["id"], record["task_id"], record["owner"], _json_dumps(record),
                 record["task_id"], record["workspace_id"], authority["workspace_root"],
                 authority["workspace_revision"], authority["instruction"],
                 _json_dumps(authority["actor"]),
                 _json_dumps(authority["scope"]), _json_dumps(authority["task_metadata"]),
                 record["node_id"], _json_dumps(authority["node_metadata"]), record["owner"]),
            )
            return changed == 1
        if record.get("schema_version", 2) != 2:
            return False
        changed = await self._db.execute(
            "INSERT OR IGNORE INTO verification_executions "
            "(id, task_id, owner, state, record_json) SELECT ?, ?, ?, 'active', ? "
            "WHERE EXISTS (SELECT 1 FROM tasks WHERE id = ? AND status = 'completed' "
            "AND json_extract(metadata_json, '$.execution_mode') = 'code')",
            (
                record["id"],
                record["task_id"],
                record["owner"],
                _json_dumps(record),
                record["task_id"],
            ),
        )
        return changed == 1

    async def finish_verification_execution(
        self, execution_id: str, owner: str, state: str, result: dict[str, Any]
    ) -> bool:
        if state not in {"completed", "uncertain"}:
            raise ValueError("Invalid verification settlement state")
        changed = await self._db.execute(
            "UPDATE verification_executions SET state = ?, result_json = ? "
            "WHERE id = ? AND owner = ? AND state = 'active'",
            (state, _json_dumps(result), execution_id, owner),
        )
        return changed == 1

    async def ensure_task_submission(self, record: dict[str, Any]) -> dict[str, Any]:
        """Freeze the first payload for an idempotency key before network I/O."""
        await self._db.execute(
            "INSERT OR IGNORE INTO task_submissions VALUES (?, ?, ?)",
            (record["id"], record["task_id"], _json_dumps(record)),
        )
        rows = await self.list_task_submissions(record["task_id"])
        return next(row for row in rows if row["id"] == record["id"])

    async def update_task_submission(self, record: dict[str, Any]) -> None:
        # A late timeout must never overwrite a confirmed result.
        await self._db.execute(
            "UPDATE task_submissions SET record_json = ? WHERE id = ? "
            "AND json_extract(record_json, '$.state') != 'resolved'",
            (_json_dumps(record), record["id"]),
        )

    async def list_task_submissions(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT record_json FROM task_submissions WHERE task_id = ? ORDER BY rowid",
            (task_id,),
        )
        return [_json_loads(row["record_json"], {}) for row in rows]

    async def synchronize_task_result(self, expected, result):
        from Sprout.storage.local.sqlite.submission_sync import synchronize_result

        if not self._db.path:
            raise ValueError("Result synchronization requires file-backed metadata")
        return synchronize_result(Path(self._db.path), expected, result)

    async def reserve_model_request(self, task_id, node_id, limit):
        from Sprout.storage.local.sqlite.model_budget import reserve

        return await reserve(self._db, task_id, node_id, limit)

    async def finish_model_request(self, request_id, state, usage):
        from Sprout.storage.local.sqlite.model_budget import finish

        return await finish(self._db, request_id, state, usage)

    async def model_budget_summary(self, task_id):
        from Sprout.storage.local.sqlite.model_budget import summary

        return await summary(self._db, task_id)

    async def model_budget_report(self, task_id, configured):
        from Sprout.storage.local.sqlite.model_budget import report

        return await report(self._db, task_id, configured)

    # -- workspaces ---------------------------------------------------------
    async def save_workspace(self, workspace: Workspace) -> None:
        manifest = workspace.manifest
        manifest_data = (
            {
                "workspace_id": manifest.workspace_id,
                "revision": manifest.revision,
                "detected_languages": list(manifest.detected_languages),
                "framework_hints": list(manifest.framework_hints),
                "entry_points": list(manifest.entry_points),
                "test_commands": list(manifest.test_commands),
                "build_commands": list(manifest.build_commands),
                "created_at": manifest.created_at.isoformat(),
            }
            if manifest is not None
            else {}
        )
        await self._db.execute(
            "INSERT OR REPLACE INTO workspaces "
            "(id, root, kind, revision, manifest_json) VALUES (?, ?, ?, ?, ?)",
            (
                workspace.id,
                str(workspace.root),
                workspace.kind.value,
                workspace.revision,
                _json_dumps(manifest_data),
            ),
        )

    async def get_workspace(self, workspace_id: str) -> Workspace | None:
        row = await self._db.fetchone("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))
        return self._row_to_workspace(row) if row else None

    async def list_workspaces(self) -> list[Workspace]:
        rows = await self._db.fetchall("SELECT * FROM workspaces ORDER BY id")
        return [self._row_to_workspace(row) for row in rows]

    @staticmethod
    def _row_to_workspace(row: Any) -> Workspace:
        manifest_data = _json_loads(row["manifest_json"], {})
        manifest = None
        if manifest_data:
            manifest = WorkspaceManifest(
                workspace_id=manifest_data["workspace_id"],
                revision=manifest_data.get("revision"),
                detected_languages=tuple(manifest_data.get("detected_languages", ())),
                framework_hints=tuple(manifest_data.get("framework_hints", ())),
                entry_points=tuple(manifest_data.get("entry_points", ())),
                test_commands=tuple(manifest_data.get("test_commands", ())),
                build_commands=tuple(manifest_data.get("build_commands", ())),
                created_at=datetime.fromisoformat(manifest_data["created_at"]),
            )
        return Workspace(
            id=row["id"],
            root=Path(row["root"]),
            kind=WorkspaceKind(row["kind"]),
            revision=row["revision"],
            manifest=manifest,
        )

    # -- tasks --------------------------------------------------------------
    async def save_task(self, task: Task) -> None:
        actor = {
            "user_id": task.actor.user_id,
            "display_name": task.actor.display_name,
            "roles": list(task.actor.roles),
            "authenticated": task.actor.authenticated,
            "source": task.actor.source,
        }
        scope = {
            "version": 1,
            "allowed_actions": sorted(task.delegation_scope.allowed_actions),
            "denied_actions": sorted(task.delegation_scope.denied_actions),
            "allowed_paths": list(task.delegation_scope.allowed_paths),
            "denied_paths": list(task.delegation_scope.denied_paths),
            "expires_at": (task.delegation_scope.expires_at.isoformat()
                           if task.delegation_scope.expires_at is not None else None),
            "max_depth": task.delegation_scope.max_depth,
            "issued_by": task.delegation_scope.issued_by,
        }
        budget = {
            "max_tokens": task.budget.max_tokens,
            "max_model_calls": task.budget.max_model_calls,
            "max_tool_calls": task.budget.max_tool_calls,
            "max_duration_seconds": task.budget.max_duration_seconds,
            "max_cost_usd": task.budget.max_cost_usd,
        }
        await self._db.execute(
            "INSERT OR REPLACE INTO tasks "
            "(id, workspace_id, instruction, actor_json, source, status, "
            "delegation_scope_json, budget_json, phases_json, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.id,
                task.workspace_id,
                task.instruction,
                _json_dumps(actor),
                task.source,
                task.status.value,
                _json_dumps(scope),
                _json_dumps(budget),
                _json_dumps([phase.value for phase in task.phases]),
                _json_dumps(dict(task.metadata)),
                task.created_at.isoformat(),
            ),
        )

    async def get_task(self, task_id: str) -> Task | None:
        row = await self._db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return self._row_to_task(row) if row else None

    async def list_tasks(self, workspace_id: str | None = None) -> list[Task]:
        if workspace_id is None:
            rows = await self._db.fetchall("SELECT * FROM tasks ORDER BY created_at DESC")
        else:
            rows = await self._db.fetchall(
                "SELECT * FROM tasks WHERE workspace_id = ? ORDER BY created_at DESC",
                (workspace_id,),
            )
        return [self._row_to_task(row) for row in rows]

    async def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        await self._db.execute("UPDATE tasks SET status = ? WHERE id = ?", (status.value, task_id))

    @staticmethod
    def _row_to_task(row: Any) -> Task:
        actor = _json_loads(row["actor_json"], {})
        try:
            scope = json.loads(row["delegation_scope_json"])
            if not isinstance(scope, dict) or type(scope.get("version", 0)) is not int:
                raise ValueError("Invalid Scope version")
            if scope.get("version", 0) not in {0, 1}:
                raise ValueError("Unsupported Scope version")
            if scope.get("version") == 1 and not {
                "allowed_actions", "denied_actions", "allowed_paths", "denied_paths",
                "expires_at", "max_depth", "issued_by",
            } <= set(scope):
                raise ValueError("Incomplete Scope record")
            for name in ("allowed_actions", "denied_actions", "allowed_paths", "denied_paths"):
                values = scope.get(name, [])
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise ValueError("Invalid Scope collection")
            expiry = scope.get("expires_at")
            depth = scope.get("max_depth", 0)
            if type(depth) is not int or depth < 0:
                raise ValueError("Invalid Scope depth")
            if not isinstance(scope.get("issued_by", ""), str):
                raise ValueError("Invalid Scope issuer")
            restored_scope = DelegationScope(
                allowed_actions=frozenset(scope.get("allowed_actions", ())),
                denied_actions=frozenset(scope.get("denied_actions", ())),
                allowed_paths=tuple(scope.get("allowed_paths", ())),
                denied_paths=tuple(scope.get("denied_paths", ())),
                expires_at=datetime.fromisoformat(expiry) if expiry is not None else None,
                max_depth=depth, issued_by=scope.get("issued_by", ""),
            )
        except (ValueError, TypeError):
            restored_scope = DelegationScope(denied_paths=("",))
        budget = _json_loads(row["budget_json"], {})
        phases = _json_loads(row["phases_json"], [])
        from Sprout.task.models import Phase

        return Task(
            id=row["id"],
            workspace_id=row["workspace_id"],
            instruction=row["instruction"],
            actor=Principal(
                user_id=actor.get("user_id", "system"),
                display_name=actor.get("display_name"),
                roles=tuple(actor.get("roles", ())),
                authenticated=actor.get("authenticated") is True,
                source=actor.get("source", ""),
            ),
            source=row["source"],
            status=TaskStatus(row["status"]),
            delegation_scope=restored_scope,
            budget=TaskBudget(**budget),
            phases=tuple(Phase(value) for value in phases),
            metadata=dict(_json_loads(row["metadata_json"], {})),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # -- execution nodes ----------------------------------------------------
    async def save_node_result_source(self, source) -> bool:
        from Sprout.execution.node_result_source import NodeResultSourceV1, payload_digest

        if type(source) is not NodeResultSourceV1:
            return False
        try:
            source = NodeResultSourceV1.from_json(source.to_json())
        except (ValueError, TypeError):
            return False
        row = await self._db.fetchone("SELECT * FROM execution_nodes WHERE id = ?",
                                      (source.node_id,))
        if row is None:
            return False
        node = self._row_to_execution_node(row)
        guard = node.metadata.get("execution_guard", {})
        if (node.task_id != source.task_id or node.type.value != source.node_type
                or node.status is not NodeStatus.COMPLETED
                or guard.get("state") != "completed" or guard.get("owner") != source.owner
                or node.result_ref is not None
                or payload_digest(node.metadata.get("output")) != source.output_digest):
            return False
        # CAS against the exact settled row. Never overwrite an existing origin.
        changed = await self._db.execute(
            "INSERT INTO node_result_sources_v1 (node_id, source_json) "
            "SELECT id, ? FROM execution_nodes WHERE id = ? AND status = 'completed' "
            "AND metadata_json = ? AND result_ref_json IS NULL "
            "ON CONFLICT(node_id) DO NOTHING",
            (source.to_json(), node.id, row["metadata_json"]),
        )
        return changed == 1 or await self.get_node_result_source(node.id) == source

    async def get_node_result_source(self, node_id: str):
        from Sprout.execution.node_result_source import NodeResultSourceV1

        row = await self._db.fetchone(
            "SELECT source_json FROM node_result_sources_v1 WHERE node_id = ?", (node_id,),
        )
        if row is None:
            return None
        try:
            return NodeResultSourceV1.from_json(row["source_json"])
        except (TypeError, ValueError):
            return None

    async def claim_execution_node(self, node: ExecutionNode, owner: str, resume_key: str) -> bool:
        """Atomically occupy a node; ownership never expires or gets stolen."""
        guard = {
            "owner": owner,
            "state": "active",
            "resume_key": resume_key,
            "started_at": datetime.now(UTC).isoformat(),
        }
        running = replace(
            node,
            status=NodeStatus.RUNNING,
            attempts=node.attempts + 1,
            metadata={**node.metadata, "execution_guard": guard},
        )
        data = node_to_dict(running)
        changed = await self._db.execute(
            "INSERT INTO execution_nodes "
            "(id, task_id, type, dependencies_json, status, attempts, budget_json, "
            "result_ref_json, metadata_json, created_at) "
            "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE EXISTS "
            "(SELECT 1 FROM tasks WHERE id = ? AND status NOT IN "
            "('completed', 'cancelled', 'rolled_back', 'failed')) "
            "ON CONFLICT(id) DO UPDATE SET status = 'running', "
            "attempts = execution_nodes.attempts + 1, "
            "metadata_json = json_set(execution_nodes.metadata_json, '$.execution_guard', json(?)) "
            "WHERE execution_nodes.task_id = excluded.task_id "
            "AND execution_nodes.type = excluded.type "
            "AND COALESCE(json_extract(execution_nodes.metadata_json, "
            "'$.execution_guard.state'), '') NOT IN ('active', 'uncertain', 'completed') "
            "AND (execution_nodes.status IN ('pending', 'ready') OR "
            "(execution_nodes.status = 'waiting' "
            "AND json_extract(execution_nodes.metadata_json, '$.output.waiting') = 1 "
            "AND json_extract(execution_nodes.metadata_json, '$.output.waiting_kind') "
            "IS NOT 'resource' "
            "AND ? != '' AND COALESCE(json_extract(execution_nodes.metadata_json, "
            "'$.execution_guard.resume_key'), '') != ?))",
            (
                node.id,
                node.task_id,
                node.type.value,
                _json_dumps(data["dependencies"]),
                "running",
                running.attempts,
                _json_dumps(data["budget"]),
                _json_dumps(data["result_ref"]) if data["result_ref"] else None,
                _json_dumps(running.metadata),
                data["created_at"],
                node.task_id,
                _json_dumps(guard),
                resume_key,
                resume_key,
            ),
        )
        return changed == 1

    async def finish_execution_node(self, node: ExecutionNode, owner: str, state: str) -> bool:
        """Publish status/output together, only while the same owner holds the node."""
        expected = {
            "completed": NodeStatus.COMPLETED,
            "waiting": NodeStatus.WAITING,
            "uncertain": NodeStatus.WAITING,
        }
        if expected.get(state) != node.status:
            raise ValueError("Invalid execution guard settlement")
        guard = {
            **node.metadata.get("execution_guard", {}),
            "owner": owner,
            "state": state,
            "settled_at": datetime.now(UTC).isoformat(),
        }
        changed = await self._db.execute(
            "UPDATE execution_nodes SET status = ?, metadata_json = ? "
            "WHERE id = ? AND task_id = ? AND type = ? AND status = 'running' "
            "AND json_extract(metadata_json, '$.execution_guard.owner') = ? "
            "AND json_extract(metadata_json, '$.execution_guard.state') = 'active'",
            (
                node.status.value,
                _json_dumps({**node.metadata, "execution_guard": guard}),
                node.id,
                node.task_id,
                node.type.value,
                owner,
            ),
        )
        return changed == 1

    async def save_execution_node(self, node: ExecutionNode) -> None:
        budget = {
            "max_attempts": node.budget.max_attempts,
            "timeout_seconds": node.budget.timeout_seconds,
            "max_tokens": node.budget.max_tokens,
        }
        result_ref = None
        if node.result_ref is not None:
            result_ref = {
                "id": node.result_ref.id,
                "store": node.result_ref.store,
                "key": node.result_ref.key,
                "content_hash": node.result_ref.content_hash,
            }
        changed = await self._db.execute(
            "INSERT INTO execution_nodes "
            "(id, task_id, type, dependencies_json, status, attempts, budget_json, "
            "result_ref_json, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id, type=excluded.type, "
            "dependencies_json=excluded.dependencies_json, status=excluded.status, "
            "attempts=excluded.attempts, budget_json=excluded.budget_json, "
            "result_ref_json=excluded.result_ref_json, metadata_json=excluded.metadata_json, "
            "created_at=excluded.created_at "
            "WHERE json_extract(execution_nodes.metadata_json, '$.execution_guard.owner') IS NULL",
            (
                node.id,
                node.task_id,
                node.type.value,
                _json_dumps(list(node.dependencies)),
                node.status.value,
                node.attempts,
                _json_dumps(budget),
                _json_dumps(result_ref) if result_ref is not None else None,
                _json_dumps(dict(node.metadata)),
                node.created_at.isoformat(),
            ),
        )
        if changed != 1:
            raise ValueError("Owned execution nodes require owner-checked settlement")

    async def get_execution_node(self, node_id: str) -> ExecutionNode | None:
        row = await self._db.fetchone("SELECT * FROM execution_nodes WHERE id = ?", (node_id,))
        return self._row_to_execution_node(row) if row else None

    async def list_execution_nodes(self, task_id: str) -> list[ExecutionNode]:
        rows = await self._db.fetchall(
            "SELECT * FROM execution_nodes WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        )
        return [self._row_to_execution_node(row) for row in rows]

    @staticmethod
    def _row_to_execution_node(row: Any) -> ExecutionNode:
        budget = _json_loads(row["budget_json"], {})
        result_data = _json_loads(row["result_ref_json"], None)
        result_ref = None
        if result_data:
            result_ref = DataRef(
                id=result_data["id"],
                store=result_data.get("store", "blob"),
                key=result_data.get("key", ""),
                content_hash=result_data.get("content_hash"),
            )
        return ExecutionNode(
            id=row["id"],
            task_id=row["task_id"],
            type=NodeType(row["type"]),
            dependencies=tuple(_json_loads(row["dependencies_json"], [])),
            status=NodeStatus(row["status"]),
            attempts=int(row["attempts"]),
            budget=NodeBudget(**budget),
            result_ref=result_ref,
            metadata=dict(_json_loads(row["metadata_json"], {})),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # -- artifacts ----------------------------------------------------------
    async def save_artifact(self, artifact: Artifact) -> None:
        existing = await self.get_artifact(artifact.id)
        incoming_report = artifact.metadata.get("growth_validation")
        if incoming_report is not None and (
            existing is None or incoming_report != existing.metadata.get("growth_validation")
        ):
            raise ValueError("Validation records require the trusted measurement handoff")
        changed = await self._db.execute(
            "INSERT INTO artifacts "
            "(id, kind, name, version, content, status, scope, evidence_json, "
            "metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, name=excluded.name, "
            "version=excluded.version, content=excluded.content, status=excluded.status, "
            "scope=excluded.scope, evidence_json=excluded.evidence_json, "
            "metadata_json=excluded.metadata_json, updated_at=excluded.updated_at "
            "WHERE json_extract(artifacts.metadata_json, '$.publication_guard.owner') IS NULL "
            "OR (json_extract(artifacts.metadata_json, '$.publication_guard.state')='completed' "
            "AND json_extract(excluded.metadata_json, '$.publication_guard')="
            "json_extract(artifacts.metadata_json, '$.publication_guard') "
            "AND excluded.content=artifacts.content AND excluded.kind=artifacts.kind "
            "AND excluded.name=artifacts.name AND excluded.version=artifacts.version "
            "AND excluded.scope=artifacts.scope AND excluded.evidence_json=artifacts.evidence_json "
            "AND ((artifacts.status='published' AND excluded.status='monitoring') "
            "OR (artifacts.status='monitoring' AND excluded.status IN ('stable','deprecated')) "
            "OR (artifacts.status='stable' AND excluded.status='deprecated')))",
            (
                artifact.id,
                artifact.kind.value,
                artifact.name,
                artifact.version,
                artifact.content,
                artifact.status.value,
                artifact.scope,
                _json_dumps(list(artifact.evidence_ids)),
                _json_dumps(dict(artifact.metadata)),
                artifact.created_at.isoformat(),
                artifact.updated_at.isoformat(),
            ),
        )

        if not changed:
            raise ValueError("Artifact publication is occupied; generic save is prohibited")

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        row = await self._db.fetchone("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
        return self._row_to_artifact(row) if row else None

    async def claim_artifact_publication(
        self, artifact: Artifact, owner: str, decided_by: str = ""
    ) -> bool:
        guard = {
            "owner": owner,
            "state": "active",
            "started_at": datetime.now(UTC).isoformat(),
            "decided_by": decided_by,
            "target": "skill:" + artifact.name.casefold()
            if artifact.kind in {ArtifactKind.SKILL, ArtifactKind.WORKFLOW}
            else "knowledge:" + artifact.id,
        }
        changed = await self._db.execute(
            "UPDATE artifacts SET metadata_json="
            "json_set(metadata_json, '$.publication_guard', json(?)) "
            "WHERE id=? AND status=? AND json(metadata_json)=json(?) AND content=? AND kind=? "
            "AND name=? AND version=? AND scope=? AND evidence_json=? "
            "AND status IN ('validated', 'pending_approval') "
            "AND json_extract(metadata_json, '$.publication_guard.owner') IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM artifacts other WHERE other.id != artifacts.id "
            "AND json_extract(other.metadata_json, '$.publication_guard.target')=? "
            "AND json_extract(other.metadata_json, '$.publication_guard.state') "
            "IN ('active', 'uncertain'))",
            (
                _json_dumps(guard),
                artifact.id,
                artifact.status.value,
                _json_dumps(dict(artifact.metadata)),
                artifact.content,
                artifact.kind.value,
                artifact.name,
                artifact.version,
                artifact.scope,
                _json_dumps(list(artifact.evidence_ids)),
                guard["target"],
            ),
        )
        return bool(changed)

    async def record_artifact_validation(self, artifact: Artifact, record: dict[str, Any]) -> bool:
        # Internal measurement handoff, not a user/model metadata import path.
        changed = await self._db.execute(
            "UPDATE artifacts SET status='validated', "
            "metadata_json=json_set(metadata_json, '$.growth_validation', json(?)) "
            "WHERE id=? AND status=? AND json(metadata_json)=json(?) AND content=? "
            "AND kind=? AND name=? AND version=? AND scope=? AND evidence_json=? "
            "AND json_extract(metadata_json, '$.publication_guard.owner') IS NULL",
            (
                _json_dumps(record),
                artifact.id,
                artifact.status.value,
                _json_dumps(dict(artifact.metadata)),
                artifact.content,
                artifact.kind.value,
                artifact.name,
                artifact.version,
                artifact.scope,
                _json_dumps(list(artifact.evidence_ids)),
            ),
        )
        return bool(changed)

    async def finish_artifact_publication(self, artifact: Artifact, owner: str, state: str) -> bool:
        if state not in {"completed", "uncertain"}:
            raise ValueError("Invalid publication guard state")
        if (state == "completed") != (artifact.status is ArtifactStatus.PUBLISHED):
            raise ValueError("Publication status does not match guard outcome")
        metadata = dict(artifact.metadata)
        metadata["publication_guard"] = {
            **metadata.get("publication_guard", {}),
            "owner": owner,
            "state": state,
        }
        changed = await self._db.execute(
            "UPDATE artifacts SET status=?, metadata_json=? WHERE id=? "
            "AND json_extract(metadata_json, '$.publication_guard.owner')=? "
            "AND json_extract(metadata_json, '$.publication_guard.state')='active'",
            (artifact.status.value, _json_dumps(metadata), artifact.id, owner),
        )
        return bool(changed)

    async def list_artifacts(self, name: str | None = None) -> list[Artifact]:
        if name is None:
            rows = await self._db.fetchall("SELECT * FROM artifacts ORDER BY updated_at DESC")
        else:
            rows = await self._db.fetchall(
                "SELECT * FROM artifacts WHERE name = ? ORDER BY version DESC", (name,)
            )
        return [self._row_to_artifact(row) for row in rows]

    @staticmethod
    def _row_to_artifact(row: Any) -> Artifact:
        return Artifact(
            id=row["id"],
            kind=ArtifactKind(row["kind"]),
            name=row["name"],
            version=row["version"],
            content=row["content"],
            status=ArtifactStatus(row["status"]),
            scope=row["scope"],
            evidence_ids=tuple(_json_loads(row["evidence_json"], [])),
            metadata=dict(_json_loads(row["metadata_json"], {})),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # -- change proposals --------------------------------------------------
    async def save_change_proposal(self, proposal: ChangeProposal) -> None:
        sandbox_data = None
        if proposal.sandbox_ref is not None:
            sandbox_data = {
                "id": proposal.sandbox_ref.id,
                "kind": proposal.sandbox_ref.kind,
                "root": str(proposal.sandbox_ref.root),
                "worktree_ref": proposal.sandbox_ref.worktree_ref,
            }
        test_data = [
            {
                "name": item.name,
                "passed": item.passed,
                "output": item.output,
                "duration_ms": item.duration_ms,
                # Must persist, or the apply gate cannot tell a withheld
                # command from a failed one once the proposal is read back.
                "executed": item.executed,
            }
            for item in proposal.test_results
        ]
        diff_data = [
            {
                "path": item.path,
                "diff_text": item.diff_text,
                "old_hash": item.old_hash,
                "new_hash": item.new_hash,
            }
            for item in proposal.diffs
        ]
        await self._db.execute(
            "INSERT OR REPLACE INTO change_proposals "
            "(id, task_id, sandbox_json, files_changed_json, test_results_json, "
            "diffs_json, risk, required_capability_diff_json, rollback_plan, status, "
            "metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal.id,
                proposal.task_id,
                _json_dumps(sandbox_data) if sandbox_data is not None else None,
                _json_dumps(list(proposal.files_changed)),
                _json_dumps(test_data),
                _json_dumps(diff_data),
                proposal.risk,
                _json_dumps(list(proposal.required_capability_diff)),
                proposal.rollback_plan,
                proposal.status.value,
                _json_dumps(dict(proposal.metadata)),
                proposal.created_at.isoformat(),
            ),
        )

    async def get_change_proposal(self, proposal_id: str) -> ChangeProposal | None:
        row = await self._db.fetchone("SELECT * FROM change_proposals WHERE id = ?", (proposal_id,))
        return self._row_to_change_proposal(row) if row else None

    async def list_change_proposals(self, task_id: str) -> list[ChangeProposal]:
        rows = await self._db.fetchall(
            "SELECT * FROM change_proposals WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )
        return [self._row_to_change_proposal(row) for row in rows]

    @staticmethod
    def _row_to_change_proposal(row: Any) -> ChangeProposal:
        sandbox_data = _json_loads(row["sandbox_json"], None)
        sandbox_ref = None
        if sandbox_data:
            sandbox_ref = SandboxRef(
                id=sandbox_data["id"],
                kind=sandbox_data["kind"],
                root=sandbox_data["root"],
                worktree_ref=sandbox_data.get("worktree_ref"),
            )
        tests = _json_loads(row["test_results_json"], [])
        diffs = _json_loads(row["diffs_json"], [])
        return ChangeProposal(
            id=row["id"],
            task_id=row["task_id"],
            sandbox_ref=sandbox_ref,
            files_changed=tuple(_json_loads(row["files_changed_json"], [])),
            test_results=tuple(
                TestResult(
                    name=item["name"],
                    passed=bool(item["passed"]),
                    output=item.get("output", ""),
                    duration_ms=float(item.get("duration_ms", 0.0)),
                    # Absent means a row written before withheld commands
                    # existed, which always executed.
                    executed=bool(item.get("executed", True)),
                )
                for item in tests
            ),
            diffs=tuple(
                DiffResult(
                    path=item["path"],
                    diff_text=item.get("diff_text", ""),
                    old_hash=item.get("old_hash"),
                    new_hash=item.get("new_hash"),
                )
                for item in diffs
            ),
            risk=row["risk"],
            required_capability_diff=tuple(_json_loads(row["required_capability_diff_json"], [])),
            rollback_plan=row["rollback_plan"],
            status=ChangeProposalStatus(row["status"]),
            metadata=dict(_json_loads(row["metadata_json"], {})),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # -- trajectory metadata ------------------------------------------------
    async def save_trajectory_metadata(self, trajectory: Trajectory) -> None:
        snapshots = [
            {
                "artifact_id": item.artifact_id,
                "artifact_version": item.artifact_version,
                "content_hash": item.content_hash,
            }
            for item in trajectory.artifact_snapshots
        ]
        await self._db.execute(
            "INSERT OR REPLACE INTO trajectories "
            "(id, task_id, workspace_id, workspace_revision, status, "
            "artifact_snapshots_json, metrics_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trajectory.id,
                trajectory.task_id,
                trajectory.workspace_id,
                trajectory.workspace_revision,
                trajectory.status.value,
                _json_dumps(snapshots),
                _json_dumps(dict(trajectory.metrics)),
                trajectory.created_at.isoformat(),
            ),
        )

    async def get_trajectory_metadata(self, trajectory_id: str) -> Trajectory | None:
        row = await self._db.fetchone("SELECT * FROM trajectories WHERE id = ?", (trajectory_id,))
        if row is None:
            return None
        snapshots = _json_loads(row["artifact_snapshots_json"], [])
        return Trajectory(
            id=row["id"],
            task_id=row["task_id"],
            workspace_id=row["workspace_id"],
            workspace_revision=row["workspace_revision"],
            status=TrajectoryStatus(row["status"]),
            artifact_snapshots=tuple(
                ArtifactSnapshot(
                    artifact_id=item["artifact_id"],
                    artifact_version=item["artifact_version"],
                    content_hash=item.get("content_hash"),
                )
                for item in snapshots
            ),
            metrics=dict(_json_loads(row["metrics_json"], {})),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


def open_metadata_store(path: str, pragmas: SqlitePragmas | None = None) -> SqliteMetadataStore:
    """Open and reserve a SEMA metadata database."""
    return SqliteMetadataStore(SqliteDatabase.open(path, pragmas))
