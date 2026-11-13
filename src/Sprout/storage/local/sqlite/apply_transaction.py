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
    if type(record) is dict and record.get("version") == 4:
        return await _claim_git_review(db, record, snapshot)
    if type(record) is dict and record.get("version") == 3:
        return await _claim_filesystem(db, record, snapshot)
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


async def _claim_filesystem(db, record, snapshot):
    """Bind the shared ledger to exact settled nodes instead of a Git proposal."""
    from Sprout.execution.filesystem_review import (
        checked_changes,
        verification_manifest,
        verification_status,
    )
    from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType

    task_id = record["task_id"]
    workspace_id = record["workspace_id"]
    review_id, evaluation_id = f"{task_id}:approval", f"{task_id}:evaluation"
    rows = []
    for node_id in (review_id, evaluation_id):
        row = await db.fetchone(
            "SELECT * FROM execution_nodes WHERE id=? AND task_id=? AND status='completed'",
            (node_id, task_id))
        if row is None:
            return False
        rows.append(row)
    review, evaluation = rows
    review_metadata = json.loads(review["metadata_json"])
    if review["type"] != "approval" or review_metadata.get(
            "execution_guard", {}).get("state") != "completed":
        return False
    try:
        evidence = review_metadata["output"]["filesystem_review"]
        payload = checked_changes(evidence, task_id, workspace_id)
    except (KeyError, TypeError, ValueError):
        return False
    verified = ExecutionNode(
        id=evaluation_id, task_id=task_id, type=NodeType(evaluation["type"]),
        status=NodeStatus.COMPLETED, metadata=json.loads(evaluation["metadata_json"]),
    )
    expected = {"version": 1, "files": [{
        "path": relative_label(change["path"]),
        "identity": change["before"]["identity"] if change["before"] else None,
        "before": change["before"]["sha256"] if change["before"] else None,
        "after": change["after"]["sha256"],
    } for change in payload["changes"]]}
    labels = [item["path"].casefold() for item in expected["files"]]
    if (json.loads(evaluation["dependencies_json"]) != [review_id]
            or snapshot != evidence["fingerprint"] or payload["conflicts"]
            or not 0 < len(labels) <= 64 or len(set(labels)) != len(labels)
            or record["proposal_id"] != f"filesystem:{task_id}:{snapshot}"
            or record["plan"] != expected or record["plan_sha256"] != fingerprint(expected)
            or verification_status(verified, evidence, task_id, workspace_id) != "passed"):
        return False
    authority = await db.fetchone(
        "SELECT actor_json,delegation_scope_json,metadata_json FROM tasks WHERE id=?", (task_id,))
    if authority is None or json.loads(authority["actor_json"]) != json.loads(
            _encode(record["principal"])):
        return False
    metadata = json.loads(authority["metadata_json"])
    bundle = metadata.get("filesystem_acceptance_v1")
    post_test = bundle is not None
    if (bool(record.get("post_apply_required")) != post_test
            or (post_test and (record.get("manifest") != verification_manifest(payload)
                              or record.get("bundle_sha256") != bundle["bundle_sha256"]
                              or verification_status(
                                  verified, evidence, task_id, workspace_id, bundle=bundle,
                              ) != "passed"))):
        return False
    stored = {**record, "operation": "apply_v1", "changeset_sha256": snapshot,
              "authority_actor_json": authority["actor_json"],
              "authority_scope_json": authority["delegation_scope_json"],
              "review_node_json": review["metadata_json"],
              "evaluation_node_json": evaluation["metadata_json"]}
    initial = {"phase": "CLAIMED", "files": {}, "reason": ""}
    return await db.execute(
        "INSERT OR IGNORE INTO isolated_git_transactions "
        "(id,proposal_id,workspace_id,owner,state,record_json,result_json,operation) "
        "SELECT ?,?,?,?,'active',?,?,'apply_v1' WHERE EXISTS ("
        "SELECT 1 FROM tasks t JOIN workspaces w ON w.id=t.workspace_id "
        "JOIN execution_nodes a ON a.task_id=t.id "
        "JOIN execution_nodes r ON r.task_id=t.id "
        "JOIN execution_nodes e ON e.task_id=t.id "
        "WHERE t.id=? AND w.id=? AND w.root=? AND t.status='running' AND t.source='cli' "
        "AND t.actor_json=? AND t.delegation_scope_json=? "
        "AND json_extract(t.actor_json,'$.authenticated')=1 "
        "AND json_extract(t.actor_json,'$.source')='cli' "
        "AND json_extract(t.metadata_json,'$.filesystem_experiment')=1 "
        "AND json_extract(t.metadata_json,'$.session_id')=? "
        "AND a.id=? AND a.type='apply' AND a.status='running' "
        "AND json_array_length(a.dependencies_json)=1 "
        "AND json_extract(a.dependencies_json,'$[0]')=e.id "
        "AND json_extract(a.metadata_json,'$.execution_guard.owner')=? "
        "AND json_extract(a.metadata_json,'$.execution_guard.state')='active' "
        "AND r.id=? AND r.status='completed' AND r.metadata_json=? "
        "AND e.id=? AND e.status='completed' AND e.metadata_json=?)",
        (record["id"], record["proposal_id"], workspace_id, record["owner"],
         _encode(stored), _encode(initial), task_id, workspace_id, record["target"],
         authority["actor_json"], authority["delegation_scope_json"], record["session_id"],
         record["node_id"], record["owner"], review_id, review["metadata_json"],
         evaluation_id, evaluation["metadata_json"]),
    ) == 1


async def _claim_git_review(db, record, review):
    """Use the existing Apply ledger with the settled foreground Git Review as source."""
    task_id = record["task_id"]
    approval_id = f"{task_id}:approval"
    evaluation_id = review.get("evaluation_node_id")
    settled = []
    for node_id, kind in ((approval_id, "approval"), (evaluation_id, "evaluation")):
        row = await db.fetchone(
            "SELECT metadata_json FROM execution_nodes WHERE id=? AND task_id=? "
            "AND status='completed' AND type=?", (node_id, task_id, kind),
        )
        if row is None:
            return False
        settled.append(row["metadata_json"])
    reviewed, evaluated = map(json.loads, settled)
    authority = await db.fetchone(
        "SELECT actor_json,delegation_scope_json,metadata_json FROM tasks WHERE id=?", (task_id,),
    )
    if authority is None:
        return False
    metadata = json.loads(authority["metadata_json"])
    files = record["plan"]["files"]
    from base64 import b64decode

    from Sprout.execution.node_result_source import payload_digest

    if (reviewed.get("output") != review
            or record["session_id"] != metadata.get("session_id")
            or not set(review["files_changed"]) <= set(
                metadata.get("coding_preparation_v1", {}).get("writes", ()))
            or record["manifest"] != review["input_manifest"]
            or any(value.get("execution_guard", {}).get("state") != "completed"
                   for value in (reviewed, evaluated))
            or (evaluated.get("output") or {}).get("verified") is not True
            or payload_digest(evaluated["output"]) != record["verification_sha256"]
            or record["verification_sha256"] != review["verification_sha256"]
            or record["bundle_sha256"] != review["bundle_sha256"]
            or record["bundle_sha256"] != metadata["host_acceptance_v2"]["bundle_sha256"]
            or record["git_baseline"] != review["git_baseline"]
            or record["git_baseline"] != metadata["host_acceptance_v2"]["repository"]["revision"]
            or record["review_diff_sha256"] != hashlib.sha256(
                review["diff"].encode("utf-8")).hexdigest()
            or record["plan_sha256"] != fingerprint(record["plan"])
            or [item["path"] for item in files] != review["files_changed"]
            or not 0 < len(files) <= 64
            or any(not _digest(item["before"]) or not _digest(item["after"])
                   or item["before"] != metadata["git_acceptance_v1"]["source_files"].get(
                       item["path"])
                   or item["after"] != review["input_manifest"].get(item["path"])
                   or relative_label(item["path"]) != item["path"]
                   or hashlib.sha256(b64decode(item["before_data"], validate=True)).hexdigest()
                   != item["before"] for item in files)
            or json.loads(authority["actor_json"]) != json.loads(_encode(record["principal"]))):
        return False
    stored = {**record, "operation": "apply_v1",
              "authority_actor_json": authority["actor_json"],
              "authority_scope_json": authority["delegation_scope_json"],
              "review_node_json": settled[0], "evaluation_node_json": settled[1],
              "evaluation_node_id": evaluation_id}
    initial = {"phase": "CLAIMED", "files": {}, "reason": ""}
    return await db.execute(
        "INSERT OR IGNORE INTO isolated_git_transactions "
        "(id,proposal_id,workspace_id,owner,state,record_json,result_json,operation) "
        "SELECT ?,?,?,?,'active',?,?,'apply_v1' WHERE EXISTS ("
        "SELECT 1 FROM tasks t JOIN workspaces w ON w.id=t.workspace_id "
        "JOIN execution_nodes a ON a.task_id=t.id "
        "JOIN execution_nodes r ON r.task_id=t.id "
        "JOIN execution_nodes e ON e.task_id=t.id "
        "WHERE t.id=? AND w.id=? AND w.root=? AND t.status='running' AND t.source='cli' "
        "AND t.actor_json=? AND t.delegation_scope_json=? AND t.metadata_json=? "
        "AND a.id=? AND a.type='apply' AND a.status='running' "
        "AND json_extract(a.metadata_json,'$.execution_guard.owner')=? "
        "AND json_extract(a.metadata_json,'$.execution_guard.state')='active' "
        "AND r.id=? AND r.status='completed' AND r.metadata_json=? "
        "AND e.id=? AND e.status='completed' AND e.metadata_json=?)",
        (record["id"], record["proposal_id"], record["workspace_id"], record["owner"],
         _encode(stored), _encode(initial), task_id, record["workspace_id"], record["target"],
         authority["actor_json"], authority["delegation_scope_json"], authority["metadata_json"],
         record["node_id"], record["owner"], approval_id, settled[0], evaluation_id, settled[1]),
    ) == 1


def _requires_retest(record):
    return record["version"] == 4 or record.get("post_apply_required") is True


def _verified_apply(record, verification):
    return (isinstance(verification, dict) and verification.get("verified") is True
            and verification.get("input_manifest") == record["manifest"]
            and verification.get("bundle_sha256") == record["bundle_sha256"])


async def observe(db, identifier):
    row = await db.fetchone("SELECT * FROM isolated_git_transactions WHERE id=? "
                            "AND operation='apply_v1'", (identifier,))
    if row is None:
        return None
    return {"record": json.loads(row["record_json"]), "physical_state": row["state"],
            **json.loads(row["result_json"])}


async def transition(db, identifier, owner, *, start=None, outcome=None, reason="",
                     verification=None, restored=None):
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
    requires_retest = _requires_retest(old["record"])
    if (verification is not None or restored is not None) and not requires_retest:
        return False
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
        rejected_v2 = status == "REJECTED" and old["record"]["version"] in {2, 3, 4}
        if rejected_v2:
            if outcome["after"] is not None and not _digest(outcome["after"]):
                raise ValueError("Observed refusal digest invalid")
        elif outcome["after"] != (None if status == "UNKNOWN" else expected):
            raise ValueError("Observed digest does not match disposition")
        new["files"][path].update(status=status, after=outcome["after"])
        new["phase"] = "UNKNOWN" if status == "UNKNOWN" else "WRITE_STARTED"
    else:
        statuses = [x["status"] for x in new["files"].values()]
        if restored is not None:
            written = {path: files[path]["before"] for path, item in new["files"].items()
                       if item["status"] == "WRITTEN"}
            if restored != written or {"WRITE_STARTED", "UNKNOWN"}.intersection(statuses):
                return False
            new["phase"] = "ROLLED_BACK" if written else "FAILED"
            new["restored"] = restored
            if verification is not None:
                new["verification"] = verification
        elif "WRITE_STARTED" in statuses:
            new["phase"] = "UNKNOWN"
        elif "UNKNOWN" in statuses:
            new["phase"] = "UNKNOWN"
        elif len(statuses) == len(files) and all(x == "WRITTEN" for x in statuses):
            if requires_retest and not _verified_apply(old["record"], verification):
                new["phase"] = "PARTIAL"
            else:
                new["phase"] = "COMPLETED"
                if requires_retest:
                    new["verification"] = verification
        elif "WRITTEN" in statuses:
            new["phase"] = "PARTIAL"
        else:
            new["phase"] = "FAILED"
    new["reason"] = str(reason)[:2048]
    physical = ("uncertain" if new["phase"] in {"UNKNOWN", "PARTIAL"} else
                "completed" if new["phase"] in {"COMPLETED", "FAILED", "ROLLED_BACK"}
                else "active")
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
    if start is not None and old["record"]["version"] in {3, 4}:
        record = old["record"]
        live_sql += (
            " AND EXISTS (SELECT 1 FROM execution_nodes r JOIN execution_nodes e "
            "ON r.task_id=e.task_id WHERE r.task_id=? AND r.id=? AND e.id=? "
            "AND r.status='completed' AND e.status='completed' "
            "AND r.metadata_json=? AND e.metadata_json=?)"
        )
        task_id = record["task_id"]
        evaluation_id = record.get("evaluation_node_id", f"{task_id}:evaluation")
        live_args += (task_id, f"{task_id}:approval", evaluation_id,
                      record["review_node_json"], record["evaluation_node_json"])
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

async def reconcile_filesystem(db):
    """Publish completed effects after a crash, without opening project files."""
    from datetime import UTC, datetime

    rows = await db.fetchall(
        "SELECT g.id,g.record_json,g.result_json,t.id AS task_id,n.id AS node_id,"
        "n.status AS node_status,n.metadata_json AS node_metadata "
        "FROM isolated_git_transactions g "
        "JOIN tasks t ON t.id=json_extract(g.record_json,'$.task_id') "
        "JOIN execution_nodes n ON n.id=json_extract(g.record_json,'$.node_id') "
        "AND n.task_id=t.id WHERE g.operation='apply_v1' AND g.state='completed' "
        "AND json_extract(g.record_json,'$.version') IN (3,4) "
        "AND json_extract(g.result_json,'$.phase')='COMPLETED' "
        "AND g.workspace_id=t.workspace_id AND t.status='running' AND n.type='apply' "
        "AND ((json_extract(g.record_json,'$.version')=3 "
        "AND json_extract(t.metadata_json,'$.filesystem_experiment')=1) "
        "OR (json_extract(g.record_json,'$.version')=4 "
        "AND json_extract(t.metadata_json,'$.host_acceptance_v2.bundle_sha256')="
        "json_extract(g.record_json,'$.bundle_sha256'))) "
        "AND json_extract(t.metadata_json,'$.session_id')="
        "json_extract(g.record_json,'$.session_id') "
        "AND json_extract(n.metadata_json,'$.execution_guard.owner')=g.owner"
    )
    recovered = 0
    for row in rows:
        record, result = json.loads(row["record_json"]), json.loads(row["result_json"])
        plan, observed = record["plan"], result["files"]
        files = plan["files"]
        verification = result.get("verification")
        if _requires_retest(record) and not _verified_apply(record, verification):
            continue
        if (not files or fingerprint(plan) != record["plan_sha256"]
                or len(observed) != len(files) or any(
                    observed.get(item["path"]) != {
                        "status": "WRITTEN", "before": item["before"], "after": item["after"],
                    } for item in files)):
            continue
        metadata = json.loads(row["node_metadata"])
        guard = metadata["execution_guard"]
        if row["node_status"] == "running" and guard["state"] == "active":
            metadata["execution_guard"] = {
                **guard, "state": "completed", "settled_at": datetime.now(UTC).isoformat(),
            }
            metadata["output"] = {"applied": True, "transaction_id": row["id"],
                                  "files": len(files), "recovered": True}
            if _requires_retest(record):
                metadata["output"]["verification"] = verification
            settled = _encode(metadata)
        elif (row["node_status"] == "completed" and guard["state"] == "completed"
              and metadata.get("output", {}).get("applied") is True
              and metadata["output"].get("transaction_id") == row["id"]):
            settled = row["node_metadata"]
        else:
            continue
        evidence = ("EXISTS (SELECT 1 FROM isolated_git_transactions WHERE id=? "
                    "AND state='completed' AND record_json=? AND result_json=?)")
        args = (row["id"], row["record_json"], row["result_json"])
        _, changed = await db.execute_batch([
            ("UPDATE execution_nodes SET status='completed',metadata_json=? "
             "WHERE id=? AND status=? AND metadata_json=? AND " + evidence
             + " AND EXISTS (SELECT 1 FROM tasks WHERE id=? AND status='running')",
             (settled, row["node_id"], row["node_status"], row["node_metadata"],
              *args, row["task_id"])),
            ("UPDATE tasks SET status='completed' WHERE id=? AND status='running' AND "
             + evidence + " AND EXISTS (SELECT 1 FROM execution_nodes WHERE id=? "
             "AND status='completed' AND metadata_json=?)",
             (row["task_id"], *args, row["node_id"], settled)),
        ])
        recovered += changed
    return recovered
