from Sprout.runtime.nodes import NodeExecutor
from Sprout.task.models import Task
from Sprout.workspace.models import Workspace


def test_plan_and_execution_prefer_supplied_content_without_removing_inspection(tmp_path):
    task = Task(instruction="Fix addition")
    workspace = Workspace(id="fixture", root=tmp_path)
    resources = ({"path": "app.py", "decision": "allow", "content": "return 0"},)
    plan = NodeExecutor._plan_prompt(task, workspace, resources)
    execution = NodeExecutor._agent_prompt(task, workspace, resources, plan="edit app.py")
    for prompt in (plan, execution):
        assert "unchanged files" in prompt
        assert "truncated" in prompt and "freshness matters" in prompt
        assert "return 0" in prompt
    assert "do not ask whether to proceed" in plan
    assert "rather than asking whether to execute" in execution
