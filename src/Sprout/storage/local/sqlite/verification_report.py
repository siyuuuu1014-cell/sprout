"""Read-only, redacted inspection of independent verification ownership."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def recovery_facts(database: Path, task_id: str) -> dict:
    """Read one consistent metadata snapshot without initializing a store."""
    if database.is_symlink() or not database.is_file():
        raise ValueError("An existing regular metadata database is required")
    resolved = database.resolve(strict=True)
    if str(resolved).startswith("\\\\"):
        raise ValueError("Network/device database paths are not supported")
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        task = connection.execute(
            "SELECT status, workspace_id, metadata_json, actor_json, source, instruction, "
            "budget_json, delegation_scope_json FROM tasks WHERE id=?",
            (task_id,)
        ).fetchone()
        if task is None:
            raise ValueError("Isolated task not found")
        workspace = connection.execute(
            "SELECT root FROM workspaces WHERE id=?", (task["workspace_id"],)
        ).fetchone()
        if workspace is None:
            raise ValueError("Isolated workspace not found")
        metadata = json.loads(task["metadata_json"])
        actor = json.loads(task["actor_json"])
        if not isinstance(metadata, dict) or not isinstance(actor, dict):
            raise ValueError("Malformed task metadata")
        task_inputs = {
            "instruction": task["instruction"], "budget": json.loads(task["budget_json"]),
            "scope": json.loads(task["delegation_scope_json"]),
            "metadata": {key: metadata.get(key) for key in
                         ("acceptance_spec", "require_acceptance_tests", "required_paths",
                          "steps", "verification_commands", "regression_fingerprint")},
        }
        task_input_fingerprint = hashlib.sha256(
            json.dumps(task_inputs, sort_keys=True).encode()).hexdigest()
        nodes = connection.execute(
            "SELECT COUNT(*) FROM execution_nodes WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        claims = connection.execute(
            "SELECT COUNT(*) FROM verification_executions WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        resumes = connection.execute(
            "SELECT COUNT(*) FROM replay_resumes WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        return {"status": task["status"], "workspace_root": workspace["root"],
                "metadata": metadata, "node_count": nodes, "claim_count": claims,
                "resume_count": resumes, "actor": actor, "source": task["source"],
                "task_input_fingerprint": task_input_fingerprint}
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise ValueError("Recovery evidence is unavailable or malformed") from exc
    finally:
        connection.close()


def execution_evidence(database: Path, task_id: str) -> dict:
    """Internal read-only evidence, not an operator-facing raw-log export."""
    if database.is_symlink() or not database.is_file():
        raise ValueError("Existing regular database required")
    resolved = database.resolve(strict=True)
    if str(resolved).startswith("\\\\"):
        raise ValueError("Network database not supported")
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT id, state, record_json, result_json FROM verification_executions "
            "WHERE task_id=?", (task_id,)
        ).fetchall()
        evidence = {}
        for identity, state, raw, output in rows:
            record = json.loads(raw)
            result = json.loads(output) if output else {}
            if (not isinstance(record, dict) or not isinstance(result, dict)
                    or record.get("id") != identity or record.get("task_id") != task_id):
                raise ValueError("Execution identity mismatch")
            evidence[identity] = {"state": state, "record": record, "result": result}
        return evidence
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise ValueError("Execution evidence unavailable") from exc
    finally:
        connection.close()


def verification_report(database: Path, execution_id: str | None = None) -> dict:
    """Never open the writable store, migrate schema, or infer permission to retry."""
    if database.is_symlink() or not database.is_file():
        raise ValueError("An existing regular metadata database is required")
    resolved = database.resolve(strict=True)
    if str(resolved).startswith("\\\\"):
        raise ValueError("Network/device database paths are not supported")
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        query = "SELECT id, task_id, state, record_json, result_json FROM verification_executions"
        parameters = ()
        if execution_id is not None:
            query += " WHERE id=?"
            parameters = (execution_id,)
        rows = connection.execute(query + " ORDER BY id", parameters).fetchall()
        items = []
        for row in rows:
            record = json.loads(row["record_json"])
            result = json.loads(row["result_json"]) if row["result_json"] else {}
            if not isinstance(record, dict) or not isinstance(result, dict):
                raise ValueError("Malformed verification evidence")
            manifest = record.get("input_manifest", {})
            if not isinstance(manifest, dict):
                raise ValueError("Malformed input manifest")
            # No raw argv, process logs, file names, or owner tokens in the operator view.
            items.append({
                "execution_id": row["id"],
                "task_id": row["task_id"],
                "state": row["state"],
                "signature": record.get("signature"),
                "input_file_count": len(manifest),
                "container_name": record.get("container_name"),
                "reason": result.get("reason"),
                "input_unchanged": result.get("input_unchanged"),
                "requires_manual_review": row["state"] != "completed",
                "trusted_execution": False,
                "retry_authorized": False,
            })
        return {
            "executions": items,
            "read_only": True,
            "validation_committed": False,
            "retry_authorized": False,
            "scope": "Persisted execution claims only; preflight waits have no claim.",
        }
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise ValueError("Verification evidence is unavailable or malformed") from exc
    finally:
        connection.close()
