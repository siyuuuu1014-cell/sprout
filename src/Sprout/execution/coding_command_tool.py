"""Node-local model interface to a host-owned coding command mapping."""

from pathlib import PurePosixPath

from Sprout.execution.coding_feedback import command_feedback, feedback_text
from Sprout.execution.isolated_model_tools import IsolatedModelPathGuard
from Sprout.execution.models import ProcessResult
from Sprout.tools.context_keys import NodeToolInvocationContext
from Sprout.tools.result import ToolExecutionUnconfirmed, ToolResult
from Sprout.tools.spec import ToolSpec


class IsolatedCodingCommandTool:
    spec = ToolSpec(name="isolated_coding_command", risk_level="high",
        description="Run one structured command in the offline coding container and import safe "
                    "changes. One attempt per node; no host execution, "
                    "Git apply or validation grant.",
        input_schema={"type": "object", "additionalProperties": False,
                      "properties": {"command": {"type": "array", "minItems": 1,
                                                 "maxItems": 128, "items": {"type": "string"}},
                                     "relative_cwd": {"type": "string", "default": "."}},
                      "required": ["command"]})

    def __init__(self, handler, *, task, node, sandbox):
        self._handler, self._task, self._node = handler, task, node
        self._sandbox = sandbox
        self._guard = IsolatedModelPathGuard(sandbox, task)
        self._used = False
        self.invocation_context = NodeToolInvocationContext(
            task_id=task.id, source=task.source, requested_by=task.actor.user_id,
            resource_scope=task.workspace_id)

    async def invoke(self, arguments):
        if not isinstance(arguments, dict) or set(arguments) - {"command", "relative_cwd"}:
            return ToolResult.denied("Only command and confined relative_cwd are accepted")
        command, relative = arguments.get("command"), arguments.get("relative_cwd", ".")
        if (not isinstance(command, (tuple, list)) or not 1 <= len(command) <= 128
                or any(not isinstance(item, str) or "\x00" in item for item in command)
                or sum(len(item) for item in command) > 48 * 1024):
            return ToolResult.denied("Bounded structured argv required")
        if (not isinstance(relative, str) or not relative or ":" in relative or "\\" in relative
                or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts
                or (relative != "." and not self._guard.allows(relative))):
            return ToolResult.denied("Existing public directory inside the node sandbox required")
        if self._used:
            return ToolResult.denied(
                "Node command already attempted; no new slot or automatic rerun")
        self._used = True
        try:
            output = await self._handler.execute(task=self._task, node=self._node,
                command=tuple(command), cwd=self._sandbox.root / relative,
                command_slot="evaluation-0")
        except Exception as exc:
            raise ToolExecutionUnconfirmed(
                "Coding command/import result requires reconciliation") from exc
        if not isinstance(output, dict) or not isinstance(output.get("process"), ProcessResult):
            raise ToolExecutionUnconfirmed("Coding command result is malformed")
        process = output["process"]
        if process.needs_approval:
            return ToolResult.needs_approval(process.approval_id)
        imported = output.get("import") or {}
        if not isinstance(imported, dict):
            raise ToolExecutionUnconfirmed("Artifact import result is malformed")
        if imported.get("approval_id"):
            return ToolResult.needs_approval(imported["approval_id"])
        feedback = command_feedback(process)
        if not process.allowed or process.exit_code != 0 or process.error or process.redactions:
            return ToolResult.failure(
                "Isolated command did not produce a trusted successful result.\n" +
                feedback_text(feedback))
        if output.get("imported") is not True:
            return ToolResult.failure("Command finished but artifact import did not complete.\n" +
                                      feedback_text(feedback))
        return ToolResult.success("Container command completed; safe changes imported. "
            "Acceptance and regression validation are still required.\n" + feedback_text(feedback),
            data={**feedback, "imported": True})
