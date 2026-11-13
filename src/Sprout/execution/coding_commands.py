"""Host-owned mutation command mapping; not the verification dispatcher.

No model-visible registration or ordinary Runtime fallback is installed here.
"""

import re
from pathlib import Path

from Sprout.evolution.coding_assembly import build_coding_session_factory, coding_task_binding
from Sprout.execution.coding_backend import _coding_capability
from Sprout.execution.docker_verification import safe_path
from Sprout.execution.models import SandboxRef
from Sprout.orchestration.models import NodeStatus, NodeType
from Sprout.security.approval import ApprovalStatus
from Sprout.task.models import TaskStatus


class PlanBoundCodingCommands:
    """Resolve the persisted sandbox, then delegate to the real guarded session.

    Return the full process/import result: exit zero alone is never proof that
    files were imported or that acceptance/regression/growth validation passed.
    """

    def __init__(self, runtime, task, *, factory=None):
        self._metadata = runtime.storage.metadata
        self._approvals = getattr(runtime, "approvals", None)
        self._binding = coding_task_binding(task)
        self._factory = factory or build_coding_session_factory(runtime, task)

    async def approval(self, manager, *, task, node):
        current = await self._metadata.get_task(task.id)
        if (current is None or current.status != TaskStatus.RUNNING
                or coding_task_binding(current) != self._binding
                or coding_task_binding(task) != self._binding):
            raise PermissionError("Persisted live coding task required")
        await self._require_owner(task, node)
        foreground = getattr(self, "foreground_git", None)
        if foreground is not None:
            return await foreground.approval(manager, task=current, node=node)
        return await manager.coding_task_capability(current, self._binding)

    async def _require_owner(self, task, node):
        current = await self._metadata.get_execution_node(node.id)
        owner = node.metadata.get("execution_guard", {}).get("owner")
        if (current is None or current.task_id != task.id or current.type != NodeType.AGENT
                or current.status != NodeStatus.RUNNING or not owner
                or current.metadata.get("execution_guard", {}).get("owner") != owner
                or current.metadata.get("execution_guard", {}).get("state") != "active"):
            raise ValueError("Persisted live coding node ownership required")

    async def execute(self, *, task, node, command, cwd, command_slot):
        if (coding_task_binding(task) != self._binding or node.task_id != task.id
                or node.type != NodeType.AGENT or node.status != NodeStatus.RUNNING
                or not isinstance(command_slot, str)
                or not re.fullmatch(r"evaluation-[0-9]+", command_slot)):
            raise ValueError("Live plan-bound AGENT and host command slot required")
        if (not isinstance(command, (tuple, list)) or not command
                or any(not isinstance(value, str) or "\x00" in value for value in command)):
            raise ValueError("Structured coding command required")
        await self._require_owner(task, node)
        nodes = await self._metadata.list_execution_nodes(task.id)
        sandboxes = [item for item in nodes if item.task_id == task.id
                     and item.type == NodeType.SANDBOX and item.status == NodeStatus.COMPLETED
                     and item.metadata.get("execution_guard", {}).get("state") == "completed"]
        if len(sandboxes) != 1:
            raise ValueError("One completed guarded sandbox required")
        output = sandboxes[0].metadata.get("output")
        if (not isinstance(output, dict)
                or not all(output.get(key) for key in ("id", "kind", "root"))):
            raise ValueError("Persisted sandbox result required")
        sandbox = SandboxRef(id=output["id"], kind=output["kind"], root=Path(output["root"]),
                             worktree_ref=output.get("worktree_ref"))
        target, working = safe_path(sandbox.root), safe_path(cwd)
        if not working.is_dir() or not working.is_relative_to(target):
            raise ValueError("Coding cwd must remain inside the persisted sandbox")
        session = self._factory(task=task, node=node, sandbox=sandbox)
        async def check(manager, actor, workspace, source):
            if (manager is not self._approvals or actor != task.actor
                    or workspace != task.workspace_id or source != task.source):
                return False
            try:
                grant = await self.approval(manager, task=task, node=node)
            except (ValueError, PermissionError):
                return False
            return grant.status is ApprovalStatus.APPROVED

        with _coding_capability(check if self._approvals is not None else None):
            return await session.execute_command(
                tuple(command), cwd=working, command_slot=command_slot,
            )
