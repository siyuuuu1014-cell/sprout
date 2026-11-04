"""Offline regressions for the three observed real-model blockers."""
import pytest

from Sprout.strategy.execution import ExecutionPlanner
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b4_cli_analysis import cli as cli
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.mark.parametrize('value', ['0', '-1', '129', 'unlimited', '1.5'])
def test_cli_budget_rejects_unbounded_or_invalid_input(value):
    from Sprout.cli.code_task import model_request_budget

    with pytest.raises(ValueError):
        model_request_budget(value)


@pytest.mark.parametrize('value', ['1', '24', '32', '128'])
def test_cli_budget_is_explicit_and_configurable(value):
    from Sprout.cli.code_task import model_request_budget

    assert model_request_budget(value) == int(value)


def test_host_write_restriction_removes_test_edit_instruction():
    instruction = ExecutionPlanner.bind_file_contract(
        'Implement repair\nImplementation steps:\n- 先补充或更新测试文件\n'
        'Test files to add or update:\n- tests/test_app.py\n'
        'Verification criteria:\n- pytest\n',
        reads=('src/app.py', 'tests/test_app.py'), writes=('src/app.py',))
    assert 'Test files to add or update' not in instruction
    assert '先补充或更新测试文件' not in instruction
    assert 'Verification criteria:\n- pytest' in instruction
    assert 'READ-only verification inputs:\n- tests/test_app.py' in instruction
    assert 'WRITE targets (exclusive):\n- src/app.py' in instruction
    assert 'Ruff' in instruction and 'frozen acceptance' in instruction


@pytest.mark.parametrize('path,admitted', [('src/main.py', True), ('src/new.py', False)])
async def test_explicit_file_followup_uses_only_recorded_resources(cli, path, admitted):
    from Sprout.runtime.dialogue import project_answer

    cli.state.inputs = ['分析 src/main.py 的依赖']
    cli.state.answers = [True]
    await cli.run()
    runtime = cli.rig.runtime
    session = await runtime.get_session(cli.rig.session.id)
    answer = await project_answer(runtime, session, cli.rig.principal,
        requested_text=f'结合刚才的分析，说明 {path} 的作用')
    assert (answer == 'CLI_PROJECT_MAIN_RESULT') is admitted
    from Sprout.message.models import Message

    context = await runtime._ordinary_conversation_context(
        Message(f'结合刚才的分析，说明 {path} 的作用', user_id='owner', session_id=session.id),
        session, {}, {})
    assert bool(context.metadata.get('project_analysis_followup')) is admitted
    selections = []

    async def normal_analysis_provider(**request):
        selections.append(request['message'].content)
        return None  # Normal selection cancelled; this grants no new source.

    await runtime._dispatch_project_analysis(
        Message(f'结合刚才的分析，分析 {path} 的依赖', user_id='owner', session_id=session.id),
        session, principal=cli.rig.principal, analysis_task=None,
        classification={'intent': 'workspace'}, analysis_task_provider=normal_analysis_provider)
    assert bool(selections) is not admitted
    assert cli.captured.opens == ['src/main.py']


async def test_twenty_four_calls_span_plan_first_attempt_and_ruff_retry(tmp_path):
    from Sprout.llm.base import ModelBudgetExhausted
    from Sprout.llm.client import invoke_model
    from Sprout.llm.task_budget import task_model_budget
    from Sprout.tests.test_persistent_model_budget import fixture, provider

    _, store, task = await fixture(tmp_path, 24)
    model = provider()
    try:
        for node, count in [('plan', 1), ('agent', 8), ('agent:1', 15)]:
            with task_model_budget(store, task.id, node, task.budget.max_model_calls):
                for _ in range(count):
                    await invoke_model(model, [])
        with task_model_budget(store, task.id, 'agent:1', 24):
            with pytest.raises(ModelBudgetExhausted):
                await invoke_model(model, [])
        assert model.chat.await_count == 24
        assert (await store.model_budget_summary(task.id))['consumed'] == 24
    finally:
        store.close()
