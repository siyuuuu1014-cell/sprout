"""Owner-claimed text artifact import through FileBroker; never whole-tree copying."""

import asyncio
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from Sprout.execution.coding_artifacts import artifact_plan


def target_hash(sandbox, name):
    root = sandbox.root.resolve()
    target = root / name
    current = root
    for part in target.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Linked import target forbidden")
    if not target.resolve().is_relative_to(root):
        raise ValueError("Import target escapes sandbox")
    if not target.exists():
        return None
    if not target.is_file() or target.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Regular bounded import target required")
    return hashlib.sha256(target.read_bytes()).hexdigest()


async def import_artifacts(metadata, broker, *, exported, baseline, oracle_paths, sandbox,
                           task, node, command_slot):
    owner = node.metadata.get("execution_guard", {}).get("owner")
    if not owner or not command_slot or node.task_id != task.id:
        raise ValueError("Live node and stable import slot required")
    root, source = sandbox.root.resolve(), exported.resolve()
    if root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError("Import target and export must not overlap")
    plan = artifact_plan(exported, baseline, oracle_paths=oracle_paths)
    identity = str(uuid5(NAMESPACE_URL, node.id + ":coding-import:" + command_slot))
    if await metadata.get_coding_execution(identity) is not None:
        raise RuntimeError("Import already occupied or settled; do not retry")
    contents = {}
    for change in plan["changes"]:
        name = change["path"]
        if target_hash(sandbox, name) != change["before_hash"]:
            raise ValueError("Target changed since baseline")
        if change["operation"] == "delete":
            grant = await broker.authorize_delete(
                sandbox, name, task_id=task.id, scope=task.delegation_scope,
                source=task.source, requested_by=task.actor.user_id)
            if not grant.wrote:
                return {"waiting": True, "applied": False, "approval_id": grant.approval_id,
                        "reason": grant.reason}
        else:
            raw = (exported / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != change["after_hash"] or b"\x00" in raw:
                raise ValueError("Changed or binary artifact unsupported")
            contents[name] = raw.decode("utf-8")
    if artifact_plan(exported, baseline, oracle_paths=oracle_paths) != plan:
        raise ValueError("Export changed before import")
    record = {"id": identity, "task_id": task.id, "node_id": node.id, "owner": owner,
              "phase": "artifact_import", "signature": hashlib.sha256(
                  json.dumps({"plan": plan, "target": str(root)},
                             sort_keys=True).encode()).hexdigest()}
    await metadata.ensure_coding_intent({**record, "baseline": baseline, "plan": plan,
                                        "target": str(root), "slot": command_slot})
    if not await metadata.claim_coding_execution(record):
        raise RuntimeError("Import node claim changed")
    changed = []
    try:
        for change in plan["changes"]:
            name = change["path"]
            if (artifact_plan(exported, baseline, oracle_paths=oracle_paths) != plan
                    or target_hash(sandbox, name) != change["before_hash"]):
                raise RuntimeError("Input or target changed during import")
            if change["operation"] == "delete":
                result = await broker.delete(
                    sandbox, name, task_id=task.id, scope=task.delegation_scope,
                    source=task.source, requested_by=task.actor.user_id)
            else:
                result = await broker.write_text(
                    sandbox, name, contents[name], task_id=task.id,
                    scope=task.delegation_scope, newline="")
            if not result.wrote or target_hash(sandbox, name) != change["after_hash"]:
                raise RuntimeError("Artifact import withheld or unconfirmed")
            changed.append(name)
        if (artifact_plan(exported, baseline, oracle_paths=oracle_paths) != plan
                or any(target_hash(sandbox, item["path"]) != item["after_hash"]
                       for item in plan["changes"])):
            raise RuntimeError("Imported artifacts changed before settlement")
        output = {"applied": True, "changed_paths": changed, "validation_committed": False}
        if not await metadata.finish_coding_execution(identity, owner, "completed", output):
            raise RuntimeError("Import settlement unconfirmed")
        return output
    except BaseException:
        try:
            await asyncio.shield(metadata.finish_coding_execution(
                identity, owner, "uncertain", {"changed_paths": changed, "applied": False}))
        except Exception:
            pass
        raise
