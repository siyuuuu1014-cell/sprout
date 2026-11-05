"""Ordinary Apply v1 records in the existing effect ledger; never authority.

No lease, reset or recovery execution. UNKNOWN/PARTIAL keep the existing
workspace/target exclusion through the physical uncertain state.
"""
import hashlib
import json
import re

from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.workspace.authorized_content import relative_label


def _encode(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _digest(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)


async def claim(db, record, snapshot):
    keys = {"version", "id", "task_id", "workspace_id", "proposal_id", "node_id", "owner",
            "target", "principal", "plan", "plan_sha256", "approval_id"}
    if type(record) is dict and record.get("version") == 2:
        keys |= {"authorization_plan", "authorization_plan_sha256"}
    if (type(record) is not dict or set(record) != keys or record["version"] not in {1, 2}
            or type(record["version"]) is not int):
        raise ValueError("Exact ordinary Apply v1 record required")
    if not all(type(record[k]) is str and record[k] for k in keys - {
            "version", "principal", "plan", "authorization_plan"}):
        raise ValueError("Bound Apply identities required")
    plan = record["plan"]
    if (type(plan) is not dict or set(plan) != {"version", "files"} or plan["version"] != 1
            or type(plan["files"]) is not list or not 0 < len(plan["files"]) <= 64
            or record["plan_sha256"] != fingerprint(plan)):
        raise ValueError("Frozen bounded Apply plan required")
    if record["version"] == 2:
        full = record["authorization_plan"]
        if (type(full) is not dict or fingerprint(full) != record["authorization_plan_sha256"]
                or full.get("proposal_id") != record["proposal_id"]
                or full.get("task_id") != record["task_id"]
                or full.get("workspace_id") != record["workspace_id"]
                or full.get("version") != 1 or full.get("target") != record["target"]
                or full.get("principal") != json.loads(_encode(record["principal"]))
                or not full.get("session_id")
                or [{k:v for k,v in f.items() if k != "new_text"} for f in full.get("files", [])]
                    != plan["files"]):
            raise ValueError("Complete authorization plan changed")
    paths = []
    for item in plan["files"]:
        if (type(item) is not dict or set(item) != {"path", "identity", "before", "after"}
                or not _digest(item["before"]) or not _digest(item["after"])):
            raise ValueError("Exact file versions required")
        from Sprout.execution.file_snapshot import ResourceIdentity, valid_identity
        if not valid_identity(ResourceIdentity(**item["identity"])):
            raise ValueError("File identity required")
        paths.append(relative_label(item["path"]))
    if len(set(x.casefold() for x in paths)) != len(paths):
        raise ValueError("Duplicate Apply paths")
    if record["version"] == 2:
        for file in record["authorization_plan"]["files"]:
            if (type(file.get("new_text")) is not str
                    or hashlib.sha256(file["new_text"].encode("utf-8")).hexdigest()
                    != file["after"]):
                raise ValueError("Authorized content digest changed")
    principal = record["principal"]
    if (type(principal) is not dict or principal.get("authenticated") is not True
            or not principal.get("user_id") or not principal.get("source")):
        raise ValueError("Authenticated subject required")
    columns = ("id", "task_id", "sandbox_json", "files_changed_json", "test_results_json",
               "diffs_json", "risk", "required_capability_diff_json", "rollback_plan",
               "status", "metadata_json", "created_at")
    if (not snapshot or set(snapshot) != set(columns)
            or snapshot["id"] != record["proposal_id"]
            or snapshot["task_id"] != record["task_id"]):
        raise ValueError("Complete persisted proposal snapshot required")
    if record["version"] == 2:
        metadata = json.loads(snapshot["metadata_json"])
        if (metadata.get("apply_plan") != record["authorization_plan"]
                or metadata.get("apply_plan_sha256") != record["authorization_plan_sha256"]
                or metadata.get("review_only") is not False
                or metadata.get("change_coverage", {}).get("status") != "complete_for_apply_plan"):
            raise ValueError("Persisted Proposal does not bind the complete plan")
    authority = await db.fetchone(
        "SELECT actor_json,delegation_scope_json FROM tasks WHERE id=?", (record["task_id"],))
    if authority is None or json.loads(authority["actor_json"]) != json.loads(_encode(principal)):
        return False
    record = {**record, "authority_actor_json": authority["actor_json"],
              "authority_scope_json": authority["delegation_scope_json"]}
    initial = {"phase": "CLAIMED", "files": {}, "reason": ""}
    changed = await db.execute(
        "INSERT OR IGNORE INTO isolated_git_transactions "
        "(id,proposal_id,workspace_id,owner,state,record_json,result_json,operation) "
        "SELECT ?,?,?,?,'active',?,?,'apply_v1' WHERE EXISTS ("
        "SELECT 1 FROM change_proposals p JOIN tasks t ON t.id=p.task_id "
        "JOIN workspaces w ON w.id=t.workspace_id JOIN execution_nodes n ON n.task_id=t.id "
        "WHERE " + " AND ".join("p." + c + " IS ?" for c in columns) +
        " AND p.status='approved' AND t.id=? AND t.workspace_id=? AND w.root=? "
        "AND t.status='running' AND n.id=? AND n.type='apply' AND n.status='running' "
        "AND json_extract(n.metadata_json,'$.execution_guard.owner')=? "
        "AND json_extract(n.metadata_json,'$.execution_guard.state')='active' "
        "AND t.actor_json=? AND t.delegation_scope_json=? "
        "AND json_extract(t.actor_json,'$.authenticated')=1 "
        "AND json_extract(t.actor_json,'$.user_id')=? "
        "AND json_extract(t.actor_json,'$.source')=? AND t.source=? "
        "AND (json_extract(t.delegation_scope_json,'$.expires_at') IS NULL OR "
        "julianday(json_extract(t.delegation_scope_json,'$.expires_at'))>julianday('now')))",
        (record["id"], record["proposal_id"], record["workspace_id"], record["owner"],
         _encode({**record, "operation": "apply_v1"}), _encode(initial),
         *(snapshot[c] for c in columns), record["task_id"],
         record["workspace_id"], record["target"], record["node_id"], record["owner"],
         record["authority_actor_json"], record["authority_scope_json"],
         principal["user_id"], principal["source"], principal["source"]))
    return changed == 1


async def observe(db, identifier):
    row = await db.fetchone("SELECT * FROM isolated_git_transactions WHERE id=? "
                            "AND operation='apply_v1'", (identifier,))
    if row is None:
        return None
    return {"record": json.loads(row["record_json"]), "physical_state": row["state"],
            **json.loads(row["result_json"])}


async def transition(db, identifier, owner, *, start=None, outcome=None, reason=""):
    """CAS the complete prior observation. No caller-supplied phase overrides.

    start identifies one planned file; outcome is its independently observed
    written/rejected/unknown result. A started file cannot be replayed.
    """
    old = await observe(db, identifier)
    if old is None or old["record"]["owner"] != owner or old["physical_state"] != "active":
        return False
    previous = {k: old[k] for k in ("phase", "files", "reason")}
    new = json.loads(_encode(previous))
    files = {x["path"]: x for x in old["record"]["plan"]["files"]}
    if start is not None:
        live = await db.fetchone(
            "SELECT 1 FROM tasks t JOIN execution_nodes n ON n.task_id=t.id "
            "WHERE t.id=? AND t.status='running' AND n.id=? AND n.type='apply' "
            "AND n.status='running' "
            "AND json_extract(n.metadata_json,'$.execution_guard.owner')=? "
            "AND json_extract(n.metadata_json,'$.execution_guard.state')='active'",
            (old["record"]["task_id"], old["record"]["node_id"], owner))
        if live is None:
            return False
        if (outcome is not None or start not in files or start in new["files"]
                or any(x["status"] == "WRITE_STARTED" for x in new["files"].values())):
            return False
        new["phase"] = "WRITE_STARTED"
        new["files"][start] = {"status": "WRITE_STARTED", "before": files[start]["before"],
                              "after": None}
    elif outcome is not None:
        if type(outcome) is not dict or set(outcome) != {"path", "status", "after"}:
            raise ValueError("Exact observed file result required")
        path, status = outcome["path"], outcome["status"]
        if (path not in new["files"] or new["files"][path]["status"] != "WRITE_STARTED"
                or status not in {"WRITTEN", "REJECTED", "UNKNOWN"}):
            return False
        expected = files[path]["after"] if status == "WRITTEN" else files[path]["before"]
        rejected_v2 = status == "REJECTED" and old["record"]["version"] == 2
        if rejected_v2:
            if outcome["after"] is not None and not _digest(outcome["after"]):
                raise ValueError("Observed refusal digest invalid")
        elif outcome["after"] != (None if status == "UNKNOWN" else expected):
            raise ValueError("Observed digest does not match disposition")
        new["files"][path].update(status=status, after=outcome["after"])
        new["phase"] = "UNKNOWN" if status == "UNKNOWN" else "WRITE_STARTED"
    else:
        statuses = [x["status"] for x in new["files"].values()]
        if "WRITE_STARTED" in statuses:
            new["phase"] = "UNKNOWN"
        elif "UNKNOWN" in statuses:
            new["phase"] = "UNKNOWN"
        elif len(statuses) == len(files) and all(x == "WRITTEN" for x in statuses):
            new["phase"] = "COMPLETED"
        elif "WRITTEN" in statuses:
            new["phase"] = "PARTIAL"
        else:
            new["phase"] = "FAILED"
    new["reason"] = str(reason)[:2048]
    physical = ("uncertain" if new["phase"] in {"UNKNOWN", "PARTIAL"} else
                "completed" if new["phase"] in {"COMPLETED", "FAILED"} else "active")
    live_sql = ""
    live_args = ()
    if start is not None:
        live_sql = (" AND EXISTS (SELECT 1 FROM tasks t JOIN execution_nodes n ON n.task_id=t.id "
                    "WHERE t.id=? AND t.status='running' AND n.id=? AND n.type='apply' "
                    "AND n.status='running' AND t.actor_json=? AND t.delegation_scope_json=? "
                    "AND json_extract(n.metadata_json,'$.execution_guard.owner')=? "
                    "AND json_extract(n.metadata_json,'$.execution_guard.state')='active' "
                    "AND (json_extract(t.delegation_scope_json,'$.expires_at') IS NULL OR "
                    "julianday(json_extract(t.delegation_scope_json,'$.expires_at'))>julianday('now')))")
        live_args = (old["record"]["task_id"], old["record"]["node_id"],
                     old["record"]["authority_actor_json"],
                     old["record"]["authority_scope_json"], owner)
    return await db.execute(
        "UPDATE isolated_git_transactions SET result_json=?,state=? WHERE id=? AND owner=? "
        "AND operation='apply_v1' AND state='active' AND result_json=?" + live_sql,
        (_encode(new), physical, identifier, owner, _encode(previous), *live_args)) == 1


async def approve_proposal(db, snapshot):
    """CAS an unchanged pending exact-plan Proposal; data, not write authority."""
    columns = ("id", "task_id", "sandbox_json", "files_changed_json", "test_results_json",
               "diffs_json", "risk", "required_capability_diff_json", "rollback_plan",
               "status", "metadata_json", "created_at")
    if (type(snapshot) is not dict or set(snapshot) != set(columns)
            or snapshot["status"] != "pending"):
        return False
    metadata = json.loads(snapshot["metadata_json"])
    plan = metadata.get("apply_plan")
    if (not isinstance(plan, dict) or plan.get("version") != 1
            or plan.get("proposal_id") != snapshot["id"]
            or plan.get("task_id") != snapshot["task_id"]
            or fingerprint(plan) != metadata.get("apply_plan_sha256")
            or metadata.get("review_only") is not False
            or metadata.get("change_coverage", {}).get("status") != "complete_for_apply_plan"):
        return False
    return await db.execute(
        "UPDATE change_proposals SET status='approved' WHERE "
        + " AND ".join(c + " IS ?" for c in columns),
        tuple(snapshot[c] for c in columns)) == 1
