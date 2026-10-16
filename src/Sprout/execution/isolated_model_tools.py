"""Host model-tool restrictions for frozen oracles; not an OS sandbox."""

from pathlib import Path, PurePosixPath

from Sprout.evolution.replay_plan import verify_plan
from Sprout.execution.acceptance import acceptance_demo_path, acceptance_test_path
from Sprout.execution.patch import PatchError, parse_patch
from Sprout.execution.sandbox_tool import SandboxListTool, SandboxSearchTool
from Sprout.execution.verification_export import EXCLUDED, private_resource
from Sprout.tools.result import ToolResult


def _parts(value):
    if not isinstance(value, str) or not value or any(c in value for c in ":\x00\n\r*?[]"):
        raise ValueError("Confined portable model path required")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Confined portable model path required")
    if any(part.endswith((".", " ")) for part in path.parts):
        raise ValueError("Ambiguous model path forbidden")
    return tuple(part.casefold() for part in path.parts)


class IsolatedModelPathGuard:
    def __init__(self, sandbox, task):
        payload = verify_plan(task.metadata.get("replay_plan"))
        if payload["schema_version"] != 5 or payload["execution_mode"] != "code":
            raise ValueError("V5 coding plan required for model tools")
        self.root = sandbox.root.resolve()
        self.sandbox = sandbox
        paths = (*payload["oracle_paths"], acceptance_test_path(task.id),
                 acceptance_demo_path(task.id), acceptance_test_path(payload["source_task_id"]),
                 acceptance_demo_path(payload["source_task_id"]))
        self.protected = tuple(_parts(path) for path in paths)

    def allows(self, name):
        try:
            parts = _parts(name)
            if any(part in EXCLUDED for part in parts):
                return False
            if any(parts[:len(prefix)] == prefix for prefix in self.protected):
                return False
            relative = Path(name.replace("\\", "/"))
            current = self.root
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    return False
            resolved = current.resolve()
            if not resolved.is_relative_to(self.root):
                return False
            canonical = _parts(resolved.relative_to(self.root).as_posix())
            if any(part in EXCLUDED for part in canonical):
                return False
            if any(canonical[:len(prefix)] == prefix for prefix in self.protected):
                return False
            return not private_resource(relative, self.root)
        except (ValueError, TypeError, OSError):
            return False


class IsolatedModelTool:
    """Validate every patch target before the first delegated I/O or approval."""

    def __init__(self, tool, guard):
        if tool.spec.name == "sandbox_list_files":
            tool = SandboxListTool(guard.sandbox, path_guard=guard.allows)
        elif tool.spec.name == "sandbox_search":
            tool = SandboxSearchTool(guard.sandbox, path_guard=guard.allows)
        self.spec = tool.spec
        self._tool, self._guard = tool, guard

    async def invoke(self, arguments):
        name = self.spec.name
        if name in {"sandbox_read_file", "sandbox_write_file", "sandbox_edit_file"}:
            if not self._guard.allows(arguments.get("path")):
                return ToolResult.denied("Frozen/private or unconfined model path")
        elif name == "sandbox_apply_patch":
            try:
                patch = arguments.get("patch")
                if not isinstance(patch, str):
                    raise PatchError("Text patch required")
                files = parse_patch(patch)
                if not files or any(not self._guard.allows(item.path)
                                    or (item.rename_from
                                        and not self._guard.allows(item.rename_from))
                                    for item in files):
                    return ToolResult.denied("Patch touches a frozen/private or unconfined path")
            except (PatchError, ValueError):
                return ToolResult.failure("Invalid isolated model patch")
        elif name not in {"sandbox_list_files", "sandbox_search"}:
            return ToolResult.denied("Isolated model tool adapter unavailable; no host fallback")
        return await self._tool.invoke(arguments)


def public_acceptance(payload):
    """Expose the test inputs/targets, never the host oracle's expected value."""
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("Frozen acceptance design required")
    return {"cases": [{key: case[key] for key in ("id", "target", "args", "kwargs", "category")
                       if key in case} for case in payload["cases"]]}
