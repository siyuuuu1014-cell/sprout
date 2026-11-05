from Sprout.runtime.nodes import NodeExecutor
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


async def test_plan_and_execution_prefer_supplied_content_without_removing_inspection(
    rig, captured,
):
    from Sprout.tests.readonly_host_fixtures import prompt_from_owned_read

    def check(executor, resources, planned):
        plan = NodeExecutor._plan_prompt(rig.task, rig.workspace, resources)
        execution = executor._agent_prompt(rig.task, rig.workspace, resources, plan=planned)
        for prompt in (plan, execution):
            assert "unchanged files" in prompt
            assert "truncated" in prompt and "freshness matters" in prompt
            assert "def main" in prompt
        assert "do not ask whether to proceed" in plan
        assert "rather than asking whether to execute" in execution

    await prompt_from_owned_read(rig, check)
