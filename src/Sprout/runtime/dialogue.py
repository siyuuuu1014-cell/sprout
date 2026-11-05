"""Owned dialogue data and references to already settled project publications.

Neither history nor an analysis reference is an execution capability.
"""
import json
import re
from dataclasses import replace
from types import SimpleNamespace

from Sprout.execution.node_result_source import payload_digest
from Sprout.orchestration.models import NodeStatus, NodeType
from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.task.models import TaskExecutionMode, TaskStatus
from Sprout.workspace.models import ResourceKind, ResourceRef


def analysis_followup(text):
    text = text.casefold()
    text = re.sub(r"(?:不要|无需|不必|不)(?:再|重新)?扫描", "", text)
    if re.search(r"重新|扫描|整个项目|当前项目|\b(?:scan|entire|whole)\b", text):
        return False
    return bool(re.search(
        r"进一步分析|继续分析|核心模块|模块之间|刚才.*分析|上一轮.*分析|之前.*分析|分析了什么|"
        r"上述.*(?:模块|分析)|(?:刚才|上一轮|之前|上述).*(?:统计|行数|注释|空行)|"
        r"\b(?:further analysis|core modules|relationships between modules)\b", text))


async def project_answer(runtime, session, principal, *, requested_text=""):
    try:
        return await _project_answer(runtime, session, principal, requested_text=requested_text)
    except (ValueError, TypeError, KeyError):
        return None  # Corrupt persisted data never acquires provenance.


async def _project_answer(runtime, session, principal, *, requested_text=""):
    """Read a historical answer only after validating its real publication chain."""
    ref = session.metadata.get("project_analysis_ref_v1")
    if type(ref) is not dict or set(ref) != {"task_id", "node_id"}:
        return None
    store = runtime.storage.metadata
    if store is None or not hasattr(store, "get_node_result_source"):
        return None
    task = await store.get_task(ref["task_id"])
    node = await store.get_execution_node(ref["node_id"])
    source = await store.get_node_result_source(ref["node_id"])
    workspace = await store.get_workspace(task.workspace_id) if task else None
    if (task is None or node is None or source is None or workspace is None
            or task.actor != principal or task.source != principal.source
            or task.metadata.get("session_id") != session.id
            or runtime.session_workspace_id(session) != workspace.id
            or task.execution_mode is not TaskExecutionMode.READ_ONLY
            or task.status is not TaskStatus.COMPLETED or task.delegation_scope.is_expired()
            or node.task_id != task.id or node.type is not NodeType.AGENT
            or node.status is not NodeStatus.COMPLETED):
        return None
    guard = node.metadata.get("execution_guard", {})
    output = node.metadata.get("output", {})
    subject = SimpleNamespace(task=task, workspace=workspace, session_id=session.id)
    if (guard.get("state") != "completed" or source.owner != guard.get("owner")
            or source.task_id != task.id or source.node_id != node.id
            or source.node_type != node.type.value or not output.get("analysis_ok")
            or source.subject_digest != runtime._node_subject_digest(subject)
            or source.output_digest != payload_digest(output)):
        return None
    resources = json.loads(source.resources_json)
    if not resources:
        return None
    # Only exact paths recorded by the settled publication may be continued.
    # A new filename, basename guess, or traversal uses normal analysis routing.
    requested = re.findall(r"[A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+", requested_text)
    recorded = {resource["path"].replace("\\", "/") for resource in resources}
    if any(path.replace("\\", "/") not in recorded for path in requested):
        return None
    # Recheck current read policy for every recorded resource. No enumeration,
    # reread, or cached ALLOW; historical output never asserts current file bytes.
    for resource in resources:
        decision = runtime._policy_engine.decide(ActionRequest(
            task_id=task.id, actor=principal, action=ActionType.FILE_READ,
            resource=ResourceRef(workspace.id, str(workspace.root / resource["path"]),
                                 ResourceKind(resource["kind"])),
            scope=task.delegation_scope.bind_paths(str(workspace.root))))
        if decision.decision is not AccessDecision.ALLOW:
            return None
    if (task != await store.get_task(task.id) or node != await store.get_execution_node(node.id)
            or source != await store.get_node_result_source(node.id)
            or workspace != await store.get_workspace(workspace.id)):
        return None
    return output["content"]


async def code_status(runtime, session, principal):
    """Use the existing owned Task query, not a model's success claim."""
    from uuid import NAMESPACE_URL, uuid5

    from Sprout.gateway.task_queries import TaskQueries

    task_id = session.metadata.get("last_code_task_id")
    if not isinstance(task_id, str) or not task_id:
        return None
    try:
        task = await TaskQueries(runtime)._owned_task(task_id, principal)
    except (PermissionError, LookupError):
        return None
    if (task.metadata.get("session_id") != session.id
            or task.workspace_id != runtime.session_workspace_id(session)):
        return None
    query = await TaskQueries(runtime).report(task.id, principal=principal)
    workspace = await runtime.get_workspace(task.workspace_id)
    if workspace is None:
        return None
    verification = query["verification"]
    historical = verification.get("historical_verification", verification)
    checks = []
    for item in historical.get("checks", []):
        check = {key: item.get(key) for key in
                 ("name", "outcome", "executed", "passed", "exit_code")}
        command = item.get("command", [])
        check["command"] = (command if len(json.dumps(command)) <= 512 else {
            "display": "frozen executor argv; full command in verification report",
            "argv_sha256": payload_digest(command)})
        checks.append(check)
    reports = []
    for proposal in await runtime.list_change_proposals(task.id):
        row = await runtime._metadata_or_raise().get_apply_transaction(
            str(uuid5(NAMESPACE_URL, "ordinary-apply:" + proposal.id)))
        reports.append({"proposal": proposal.id, "status": proposal.status.value,
                        "files": list(proposal.files_changed),
                        "apply_phase": row.get("phase") if row else "NOT_STARTED",
                        "receipt_sha256": proposal.metadata.get("apply_plan", {}).get(
                            "receipt_sha256", "NOT_AVAILABLE")})
    return ("Persisted Task observation (not execution authority). These observations supersede "
        "earlier Agent claims about execution. A bound Workspace is not a file access grant. "
        "Task completed and Proposal applied do NOT prove tests passed. Report the checks "
        "below separately; missing evidence is NOT_AVAILABLE. Receipt references are historical, "
        "not a restored live credential.\n" + json.dumps({
        "session_id": session.id,
        "workspace": {"id": workspace.id, "root": str(workspace.root), "bound": True},
        "task": task.id, "instruction": task.instruction, "status": task.status.value,
        "proposals": reports,
        "verification": {"source": "TaskQueries.report/get_verification_report",
            "status": historical.get("status", "NOT_AVAILABLE") if checks else "NOT_AVAILABLE",
            "verified": historical.get("verified", False), "checks": checks,
            "current_applicability": verification.get("current_applicability", "NOT_AVAILABLE"),
            "evaluation_node_id": verification.get("evaluation_node_id"),
            "failure_codes": historical.get("failure_codes", [])}}, ensure_ascii=False))


def history_question(text):
    """Only state questions; negated execution wording does not request new work."""
    text = text.casefold()
    text = re.sub(
        r"(?:不要|不必|无需|不)(?:再|重新)?(?:执行任务|执行|修改文件|修改代码|扫描)", "", text)
    if re.search(r"修复|新增|实现|重新执行|重新运行|分析当前项目|分析这个项目|"
                 r"\b(?:fix|implement|rerun|analyze the project)\b", text):
        return False
    return bool(re.search(
        r"上一轮.*修改|上次.*任务|刚才.*任务|测试.*(?:通过|结果)|apply.*(?:成功|结果)|"
        r"当前\s*workspace|\b(?:previous task|last task|tests pass|apply status)\b", text))


def dialogue_window(turns, session_id, settings):
    """Chronological, bounded text history; omit raw tools and all metadata."""
    remaining = max(128, int((settings.token_budget or 16384) * settings.history_ratio
                             * settings.char_per_token))
    selected = []
    for turn in reversed(sorted(turns, key=lambda item: item.seq)):
        if turn.session_id != session_id or turn.role not in {"user", "assistant"}:
            continue
        content = turn.content
        if len(content) > remaining:
            content = content[:remaining] + "\n[history truncated]"
        selected.append(replace(turn, content=content, metadata={}))
        remaining -= len(content)
        if remaining <= 0:
            break
    return tuple(reversed(selected))
