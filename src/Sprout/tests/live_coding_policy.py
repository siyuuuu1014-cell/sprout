"""Explicit isolated trial policy; never installed in production runtime."""

from Sprout.security.access import AccessDecision, ActionType, PolicyDecision
from Sprout.tests.test_isolated_coding_temporal_integration import CodingFixturePolicy

MODEL_FILE_TOOLS = frozenset({"sandbox_read_file", "sandbox_write_file",
    "sandbox_edit_file", "sandbox_apply_patch", "sandbox_list_files", "sandbox_search"})


class LiveCodingPolicy(CodingFixturePolicy):
    """Allow dispatch to guarded node-local tools, not arbitrary host tools.

IsolatedModelTool still validates paths; FileBroker still confines writes.
SANDBOX_ONLY is a file-broker decision, not a ToolExecutor dispatch grant.
"""

    def decide(self, request):
        if (request.action == ActionType.TOOL_INVOKE
                and request.arguments.get("tool_name") in MODEL_FILE_TOOLS):
            return PolicyDecision(AccessDecision.ALLOW, "explicit guarded experiment model tool")
        return super().decide(request)
