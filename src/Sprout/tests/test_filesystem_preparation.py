from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.cli.filesystem_preparation import plan_files, resolve_image
from Sprout.tests.test_r04_b4_cli_auto import automatic as automatic
from Sprout.tests.test_r04_b4_cli_auto import captured as captured
from Sprout.tests.test_r04_b4_cli_auto import cli as cli
from Sprout.tests.test_r04_b4_cli_auto import rig as rig


def test_existing_tests_are_selected_for_update():
    plan = plan_files(('app.py', 'test_app.py'), '为当前项目生成单元测试')
    assert plan['reads'] == ('app.py', 'test_app.py')
    assert plan['writes'] == ('test_app.py',)


def test_missing_tests_and_multiple_modules():
    plan = plan_files(('app.py', 'utils.py'), '为 app.py 和 utils.py 生成单元测试')
    assert plan['writes'] == ('test_app.py', 'test_utils.py')


def test_nested_existing_tests_are_preferred():
    plan = plan_files(('src/app.py', 'tests/test_app.py'), '为 src/app.py 生成测试')
    assert plan['writes'] == ('tests/test_app.py',)


async def test_cached_compatible_image_is_pinned(monkeypatch):
    monkeypatch.delenv('SPROUT_FILESYSTEM_CODING_IMAGE', raising=False)
    image = 'sha256:' + 'a' * 64
    inspect = SimpleNamespace(allowed=True, exit_code=0, stdout='[{"Os":"linux",'
        '"Config":{"Labels":{"sprout.role":"isolated-coding","sprout.coding.schema":"1"}}}]')
    run = AsyncMock(side_effect=[SimpleNamespace(allowed=True, exit_code=0),
                                SimpleNamespace(allowed=True, exit_code=0, stdout=image), inspect])
    assert await resolve_image(run) == image
    assert all(call.args[0][1] != 'run' for call in run.await_args_list)


async def test_engine_failure_is_reported_before_image_lookup():
    run = AsyncMock(return_value=SimpleNamespace(
        allowed=False, exit_code=None, error='unavailable'))
    with pytest.raises(PermissionError, match='docker_engine_unavailable'):
        await resolve_image(run)
    assert run.await_count == 1


async def test_missing_image_does_not_pull(monkeypatch):
    monkeypatch.delenv('SPROUT_FILESYSTEM_CODING_IMAGE', raising=False)
    run = AsyncMock(side_effect=[SimpleNamespace(allowed=True, exit_code=0),
                                SimpleNamespace(allowed=True, exit_code=0, stdout='')])
    with pytest.raises(PermissionError, match='docker_compatible_image_unavailable'):
        await resolve_image(run)
    assert run.await_count == 2


@pytest.mark.parametrize(('instruction', 'writes', 'execution'), [
    ('为当前项目生成单元测试',
     {'tests/test_main.py', 'tests/test_worker.py', 'tests/test_outside.py'}, ()),
    ('修改 src/main.py 中的函数，并运行测试', {'src/main.py'}, ('run_tests',)),
])
async def test_discovery_uses_real_dispatch_signature_and_authorized_publication(
    automatic, instruction, writes, execution,
):
    from Sprout.cli.filesystem_preparation import discover_plan
    from Sprout.message.models import Message

    cli = automatic
    cli.state.answers = [True]
    cli.state.expected_prompt_classifications = 0
    runtime = cli.rig.runtime
    session = cli.rig.session
    principal = runtime._sessions.frozen_principal(session)
    await runtime._remember_session_workspace(session.id, cli.rig.workspace.id)
    result = await discover_plan(runtime, cli.rig.workspace,
        Message(instruction, channel='cli', session_id=session.id,
                user_id=principal.user_id), session, principal)
    assert set(result['reads']) == {'src/main.py', 'src/worker.py', 'src2/outside.py'}
    assert set(result['writes']) == writes
    assert result['execution'] == execution


@pytest.mark.parametrize('different_session', [False, True])
async def test_status_is_readonly_owned_and_accepts_list_node_output(tmp_path, different_session):
    import json

    from Sprout.cli.filesystem_preparation import readonly_status
    from Sprout.gateway.identity import Principal
    from Sprout.orchestration.models import ExecutionNode, NodeType
    from Sprout.task.models import Task

    actor = Principal('owner', authenticated=True, source='cli')
    task = Task(workspace_id='workspace', actor=actor,
                metadata={'session_id': 'other' if different_session else 'session'})
    node = ExecutionNode(task_id=task.id, type=NodeType.AGENT, metadata={'output': []})
    store = SimpleNamespace(list_execution_nodes=AsyncMock(return_value=[node]),
        get_coding_execution=AsyncMock(return_value={'task_id': task.id, 'node_id': node.id,
            'state': 'completed', 'result': {'exit_code': 0, 'started': True}}))
    runtime = SimpleNamespace(
        _sessions=SimpleNamespace(require_owner=AsyncMock(return_value=SimpleNamespace(id='session')),
                                  frozen_principal=lambda _: actor),
        session_workspace_id=lambda _: 'workspace', list_tasks=AsyncMock(return_value=[task]),
        get_task=AsyncMock(return_value=task), _metadata_or_raise=lambda: store,
        _local_cli_diagnostics=None, execute=AsyncMock())
    if different_session:
        with pytest.raises(LookupError):
            await readonly_status(runtime, 'session', actor)
    else:
        result = json.loads(await readonly_status(runtime, 'session', actor))
        assert result[0]['read_only'] is True
        assert result[0]['nodes'][0]['reason'] == ''
        assert result[0]['coding'][0]['state'] == 'completed'
        assert result[0]['coding'][0]['live_container_state'] == 'NOT_QUERIED'
    runtime.execute.assert_not_awaited()


@pytest.mark.parametrize('changed_binding', [False, True])
async def test_preflight_preserves_deny_and_session_binding(tmp_path, monkeypatch, changed_binding):
    from Sprout.cli.filesystem_preparation import preflight
    from Sprout.execution.process_broker import ProcessBroker
    from Sprout.gateway.identity import Principal
    from Sprout.security.access import AccessDecision, PolicyDecision
    from Sprout.workspace.models import Workspace

    actor = Principal('owner', authenticated=True, source='cli')
    workspace = Workspace('workspace', tmp_path)
    owned = SimpleNamespace(id='session')
    spawn = AsyncMock(side_effect=AssertionError('DENY must not launch'))
    monkeypatch.setattr('asyncio.create_subprocess_exec', spawn)
    broker = ProcessBroker(SimpleNamespace(decide=lambda _: PolicyDecision(AccessDecision.DENY)))
    runtime = SimpleNamespace(_process_broker=broker, _local_cli_diagnostics=None,
        _sessions=SimpleNamespace(require_owner=AsyncMock(return_value=owned),
                                  frozen_principal=lambda _: actor),
        get_workspace=AsyncMock(return_value=workspace),
        session_workspace_id=lambda _: 'other' if changed_binding else workspace.id)
    with pytest.raises(PermissionError, match='subject_changed' if changed_binding else
                       'docker_engine_unavailable'):
        await preflight(runtime, workspace, owned, actor)
    spawn.assert_not_awaited()


async def test_docker_failure_prevents_coding_task_creation(tmp_path, monkeypatch):
    from Sprout.cli.filesystem_task import prepare_filesystem
    from Sprout.gateway.identity import Principal
    from Sprout.orchestration.terminal import LocalOrchestratorBackend
    from Sprout.workspace.models import Workspace

    async def unavailable(*_):
        raise PermissionError('docker_engine_unavailable')
    monkeypatch.setattr('Sprout.cli.filesystem_preparation.preflight', unavailable)
    runtime = SimpleNamespace(_orchestrator_backend=LocalOrchestratorBackend(None),
        session_workspace_id=lambda _: 'workspace', create_task=AsyncMock())
    with pytest.raises(PermissionError, match='docker_engine_unavailable'):
        await prepare_filesystem(runtime, Workspace('workspace', tmp_path),
            SimpleNamespace(content='生成测试'), SimpleNamespace(id='session'),
            Principal('owner', authenticated=True, source='cli'))
    runtime.create_task.assert_not_awaited()


@pytest.mark.parametrize('path', ['../app.py', '.env'])
async def test_plan_does_not_admit_unapproved_or_private_targets(automatic, path):
    from Sprout.cli.filesystem_preparation import discover_plan
    from Sprout.message.models import Message

    cli = automatic
    cli.state.answers = [True]
    cli.state.expected_prompt_classifications = 0
    runtime = cli.rig.runtime
    session = cli.rig.session
    principal = runtime._sessions.frozen_principal(session)
    await runtime._remember_session_workspace(session.id, cli.rig.workspace.id)
    with pytest.raises((PermissionError, ValueError)):
        await discover_plan(runtime, cli.rig.workspace,
            Message('修改 ' + path, channel='cli', session_id=session.id,
                    user_id=principal.user_id), session, principal)


async def test_diagnostic_preparation_never_prompts_for_paths_or_image(
    tmp_path, monkeypatch, capsys,
):
    from Sprout.cli.filesystem_task import prepare_filesystem
    from Sprout.gateway.identity import Principal
    from Sprout.orchestration.terminal import LocalOrchestratorBackend
    from Sprout.task.models import Task
    from Sprout.workspace.models import Workspace

    actor = Principal('owner', authenticated=True, source='cli')
    workspace = Workspace('workspace', tmp_path)
    session = SimpleNamespace(id='session')
    task = Task(workspace_id=workspace.id, actor=actor, source='cli')
    store = SimpleNamespace(save_task=AsyncMock())
    runtime = SimpleNamespace(_orchestrator_backend=LocalOrchestratorBackend(None),
        _sessions=SimpleNamespace(require_owner=AsyncMock(return_value=session),
                                  frozen_principal=lambda _: actor),
        session_workspace_id=lambda _: workspace.id,
        get_workspace=AsyncMock(return_value=workspace),
        create_task=AsyncMock(return_value=task), _metadata_or_raise=lambda: store,
        _cli_code_writes={}, _sandbox_lifecycle=SimpleNamespace(filesystem={}))
    monkeypatch.setattr('Sprout.cli.filesystem_preparation.preflight',
                        AsyncMock(return_value='sha256:' + 'a' * 64))
    monkeypatch.setattr('Sprout.cli.filesystem_preparation.discover_plan', AsyncMock(
        return_value={'reads': ('app.py', 'test_app.py'), 'writes': ('test_app.py',),
                      'execution': ('run_tests',),
                      'actions': ('test_extension', 'test_execution')}))
    monkeypatch.setattr('Sprout.security.local_diagnostics.current', lambda _: object())
    prompt = AsyncMock(side_effect=AssertionError('No manual path/image prompts'))
    monkeypatch.setattr('questionary.text', prompt)
    monkeypatch.setattr('questionary.confirm', prompt)
    monkeypatch.delenv('SPROUT_FILESYSTEM_VERIFICATION_SPEC', raising=False)
    result = await prepare_filesystem(runtime, workspace, SimpleNamespace(content='生成测试'),
                                      session, actor)
    assert result.id == task.id
    plan = runtime.create_task.call_args.kwargs['metadata']['filesystem_plan_v1']
    assert plan['operations'] == [{'path': 'test_app.py', 'operation': 'update'}]
    assert plan['execution'] == ['run_tests']
    assert plan['actions'] == ['test_extension', 'test_execution']
    assert '[Planning] execute: run_tests' in capsys.readouterr().out
    assert runtime._cli_code_writes[task.id][3] == ('test_app.py',)
    prompt.assert_not_called()
    runtime._sandbox_lifecycle.filesystem[task.id].evidence.rmdir()


@pytest.mark.parametrize(('request_text', 'writes', 'execution', 'actions'), [
    ('修改 app.py 中的函数，并运行测试', ('app.py',), ('run_tests',),
     ('code_modification', 'test_execution')),
    ('修改 app.py 并补充对应测试', ('app.py', 'test_app.py'), (),
     ('code_modification', 'test_extension')),
    ('为 app.py 生成单元测试', ('test_app.py',), (), ('test_generation',)),
    ('补充现有 test_app.py 的覆盖率', ('test_app.py',), (), ('test_extension',)),
    ('只运行当前项目测试', (), ('run_tests',), ('test_execution',)),
    ('修改 app.py 和 utils.py 并运行测试', ('app.py', 'utils.py'), ('run_tests',),
     ('code_modification', 'test_execution')),
    ('只讨论如何编写测试', (), (), ()),
    ('修改 app.py 和 test_app.py，并运行测试', ('app.py', 'test_app.py'), ('run_tests',),
     ('code_modification', 'test_extension', 'test_execution')),
    ('修改 app.py 并为 utils.py 生成测试', ('app.py', 'test_utils.py'), (),
     ('code_modification', 'test_generation')),
    ('不要修改 app.py，也不要运行测试', (), (), ()),
    ('修改 app.py 并添加日志后运行测试', ('app.py',), ('run_tests',),
     ('code_modification', 'test_execution')),
])
def test_file_writes_and_test_execution_are_separate(request_text, writes, execution, actions):
    paths = ('app.py', 'test_app.py', 'utils.py')
    plan = plan_files(paths, request_text)
    assert plan['reads'] == paths
    assert plan['writes'] == writes
    assert plan['execution'] == execution
    assert plan['actions'] == actions


@pytest.mark.parametrize('request_text', [
    '修改 missing.py 中的函数，并运行测试',
    '为 missing.py 生成单元测试',
    '补充现有 missing_test.py 的覆盖率',
    '为 absent/app.py 生成单元测试',
])
def test_missing_explicit_file_never_falls_back_to_other_sources(request_text):
    with pytest.raises(PermissionError, match='filesystem_planning_file_unavailable'):
        plan_files(('app.py', 'test_app.py'), request_text)
