from types import SimpleNamespace

from Sprout.execution.models import SandboxRef
from Sprout.execution.sandbox_tool import (
    SandboxGitTool,
    SandboxListTool,
    SandboxReadTool,
    SandboxSearchTool,
)
from Sprout.runtime.nodes import NodeExecutor
from Sprout.task.models import Task, TaskExecutionMode


def test_local_code_does_not_advertise_unbrokered_sandbox_reads(tmp_path):
    sandbox = SandboxRef(id="fixture", kind="git_worktree", root=tmp_path)
    read = SandboxReadTool(sandbox, SimpleNamespace())
    tools = [read, SandboxListTool(sandbox), SandboxSearchTool(sandbox), SandboxGitTool(sandbox)]
    executor = SimpleNamespace(_isolated_coding_required=False)
    task = Task(metadata={"execution_mode": TaskExecutionMode.CODE})
    selected, resources = NodeExecutor._model_access(executor, task, sandbox, tools, ())
    assert selected == [read]
    assert resources == ()


def test_readonly_project_tool_wiring_is_unchanged():
    executor = SimpleNamespace(_isolated_coding_required=False)
    tools = [object()]
    assert NodeExecutor._model_access(executor, Task(metadata={"execution_mode": "read_only"}),
                                     None, tools, ()) == (tools, ())
