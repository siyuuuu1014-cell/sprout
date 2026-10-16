"""Host-owned mutation command mapping; not the verification dispatcher.

No model-visible registration or ordinary Runtime fallback is installed here.
"""

import re
from pathlib import Path

from Sprout.evolution.coding_assembly import build_coding_session_factory, coding_task_binding
from Sprout.execution.docker_verification import safe_path
from Sprout.execution.models import SandboxRef
from Sprout.orchestration.models import NodeStatus, NodeType


class PlanBoundCodingCommands:
    """Resolve the persisted sandbox, then delegate to the real guarded session.

    Return the full process/import result: exit zero alone is never proof that
    files were imported or that acceptance/regression/growth validation passed.
    """

    def __init__(self, runtime, task):
        self._metadata = runtime.storage.metadata
        self._binding = coding_task_binding(task)
        self._factory = build_coding_session_factory(runtime, task)

    async def execute(self, *, task, node, command, cwd, command_slot):
        if (coding_task_binding(task) != self._binding or node.task_id != task.id
                or node.type != NodeType.AGENT or node.status != NodeStatus.RUNNING
                or not isinstance(command_slot, str)
                or not re.fullmatch(r"evaluation-[0-9]+", command_slot)):
            raise ValueError("Live plan-bound AGENT and host command slot required")
        if (not isinstance(command, (tuple, list)) or not command
                or any(not isinstance(value, str) or "\x00" in value for value in command)):
            raise ValueError("Structured coding command required")
        current = await self._metadata.get_execution_node(node.id)
        owner = node.metadata.get("execution_guard", {}).get("owner")
        if (current is None or current.task_id != task.id or current.type != NodeType.AGENT
                or current.status != NodeStatus.RUNNING or not owner
                or current.metadata.get("execution_guard", {}).get("owner") != owner
                or current.metadata.get("execution_guard", {}).get("state") != "active"):
            raise ValueError("Persisted live coding node ownership required")
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
        return await session.execute_command(tuple(command), cwd=working, command_slot=command_slot)
