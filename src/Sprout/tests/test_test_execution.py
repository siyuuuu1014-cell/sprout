from Sprout.intents import detect_task_hint
from Sprout.runtime.isolated_test_run import execution_report as report
from Sprout.runtime.isolated_test_run import execution_request as matches
from Sprout.tests.test_r04_b4_cli_auto import automatic as automatic
from Sprout.tests.test_r04_b4_cli_auto import captured as captured
from Sprout.tests.test_r04_b4_cli_auto import cli as cli
from Sprout.tests.test_r04_b4_cli_auto import rig as rig


def test_pure_execution_is_not_a_write_request():
    text = '只运行当前项目已有的单元测试，不修改任何文件，使用 Docker 隔离环境，返回真实测试结果。'
    assert matches(text)
    assert not detect_task_hint(text)


def test_other_requests_do_not_use_execution_only_route():
    for text in ('生成测试并运行', '修改 app.py 并运行测试', '只讨论测试方案', '不要运行测试'):
        assert not matches(text)


def test_nonzero_exit_is_never_passed():
    value = report(('python', '-m', 'pytest'), 'sha256:fixture', 1,
                   'SPROUT_TEST_RESULT={"total":2,"passed":1,"failed":1,"skipped":0}\n', 'error')
    assert value['status'] == 'failed'
    assert value['failed'] == 1
    assert value['stderr'] == 'error'


def test_real_zero_exit_and_counts():
    value = report(('python', '-m', 'unittest'), 'sha256:fixture', 0,
                   'SPROUT_TEST_RESULT={"total":2,"passed":2,"failed":0,"skipped":0}\n', '')
    assert value['status'] == 'passed'
    assert value['passed'] == 2


def test_runner_real_success_and_failure_counts(tmp_path):
    import subprocess
    import sys

    from Sprout.runtime.isolated_test_run import runner_command as command

    path = tmp_path / 'test_example.py'
    for assertion, expected in [('True', 'passed'), ('False', 'failed')]:
        path.write_text('import unittest\nclass Checks(unittest.TestCase):\n'
                        ' def test_value(self): self.assertTrue(' + assertion + ')\n')
        argv = command('unittest', ('test_example.py',))
        process = subprocess.run((sys.executable, *argv[1:]), cwd=tmp_path,
                                 text=True, capture_output=True, check=False)
        value = report(argv, 'fixture', process.returncode, process.stdout, process.stderr)
        assert value['status'] == expected
        assert value['total'] == 1
        assert value['failed'] == (expected == 'failed')


async def test_no_existing_tests_prevents_task_creation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import pytest

    from Sprout.cli.filesystem_task import prepare_filesystem
    from Sprout.gateway.identity import Principal
    from Sprout.orchestration.terminal import LocalOrchestratorBackend
    from Sprout.workspace.models import Workspace

    runtime = SimpleNamespace(_orchestrator_backend=LocalOrchestratorBackend(None),
        session_workspace_id=lambda _: 'workspace', create_task=AsyncMock())
    monkeypatch.setattr('Sprout.cli.filesystem_preparation.preflight',
                        AsyncMock(return_value='sha256:'+'a'*64))
    monkeypatch.setattr('Sprout.cli.filesystem_preparation.discover_plan',
                        AsyncMock(return_value={'reads': ('app.py',), 'writes': (),
                                                'execution': ('run_tests',)}))
    with pytest.raises(PermissionError, match='project_tests_not_found'):
        await prepare_filesystem(runtime, Workspace('workspace', tmp_path),
            SimpleNamespace(content='只运行测试'), SimpleNamespace(id='session'),
            Principal('owner', authenticated=True, source='cli'), test_only=True)
    runtime.create_task.assert_not_awaited()


async def test_runtime_routes_nonwriting_execution_before_agent(automatic):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.message.models import Message

    runtime = automatic.rig.runtime
    session = automatic.rig.session
    principal = runtime._sessions.frozen_principal(session)
    message = Message('只运行已有测试，不修改任何文件', channel='cli', session_id=session.id,
                      user_id=principal.user_id)
    context = SimpleNamespace(metadata={})
    classification = await runtime._classify_intent_with_llm(message, context)
    assert classification['reason'] == 'test_execution'
    provider = AsyncMock(return_value=None)
    response, held = await runtime._dispatch_task_intent(message, context, session,
        principal=principal, foreground=True, code_task_provider=provider)
    assert response.content == 'test_execution_cancelled'
    assert held is None
    provider.assert_awaited_once()


def test_pytest_runner_real_failure_counts(tmp_path):
    import subprocess
    import sys

    from Sprout.runtime.isolated_test_run import runner_command as command

    (tmp_path / 'test_sample.py').write_text('def test_ok(): assert True\n'
                                            'def test_bad(): assert False\n')
    argv = command('pytest', ('test_sample.py',))
    process = subprocess.run((sys.executable, *argv[1:]), cwd=tmp_path,
                             text=True, capture_output=True, check=False)
    result = report(argv, 'fixture', process.returncode, process.stdout, process.stderr)
    assert result['status'] == 'failed'
    assert (result['total'], result['passed'], result['failed']) == (2, 1, 1)


def test_production_module_is_not_a_pytest_candidate():
    from pathlib import Path

    import Sprout.runtime.isolated_test_run as module

    assert not Path(module.__file__).name.startswith('test_')
    assert not any(name.startswith('test_') and callable(value)
                   for name, value in vars(module).items())


def test_framework_uses_syntax_configuration_and_availability():
    from types import SimpleNamespace

    import pytest

    from Sprout.runtime.isolated_test_run import choose_framework

    def snapshots(files):
        return {path: SimpleNamespace(data=text.encode()) for path, text in files.items()}
    plain = {'test_example.py': 'def test_value(): assert True\n'}
    unit = {'test_unit.py': 'import unittest\nclass Checks(unittest.TestCase):\n'
                           ' def test_value(self): self.assertTrue(True)\n'}
    assert choose_framework(snapshots(plain), tuple(plain), {'pytest', 'unittest'}) == 'pytest'
    assert choose_framework(snapshots(unit), tuple(unit), {'pytest', 'unittest'}) == 'unittest'
    mixed = {**plain, **unit}
    assert choose_framework(snapshots(mixed), tuple(mixed), {'pytest', 'unittest'}) == 'pytest'
    configured = {**unit, 'pyproject.toml': '[tool.pytest.ini_options]\naddopts="-q"\n'}
    assert choose_framework(snapshots(configured), tuple(unit), {'pytest'}) == 'pytest'
    commented = {'test_unit.py': '# pytest is mentioned only in a comment\n' + unit['test_unit.py']}
    assert choose_framework(snapshots(commented), tuple(unit), {'pytest', 'unittest'}) == 'unittest'
    with pytest.raises(PermissionError, match='pytest_runner_unavailable'):
        choose_framework(snapshots(mixed), tuple(mixed), {'unittest'})


def test_zero_count_is_not_a_pass():
    result = report(('python',), 'fixture', 0,
        'SPROUT_TEST_RESULT={"total":0,"passed":0,"failed":0,"skipped":0}\n', '')
    assert result['status'] != 'passed'


def test_selected_mixed_runner_executes_plain_and_unittest_tests(tmp_path):
    import subprocess
    import sys
    from types import SimpleNamespace

    from Sprout.runtime.isolated_test_run import choose_framework, runner_command

    files = {'test_plain.py': 'def test_plain(): assert 2 + 2 == 4\n',
             'test_unit.py': 'import unittest\nclass Checks(unittest.TestCase):\n'
                             ' def test_unit(self): self.assertEqual(1, 1)\n'}
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    snapshots = {p: SimpleNamespace(data=v.encode()) for p, v in files.items()}
    framework = choose_framework(snapshots, tuple(files), {'pytest', 'unittest'})
    argv = runner_command(framework, tuple(files))
    process = subprocess.run((sys.executable, *argv[1:]), cwd=tmp_path,
                             capture_output=True, text=True, check=False)
    result = report(argv, 'fixture', process.returncode, process.stdout, process.stderr)
    assert framework == 'pytest'
    assert result['status'] == 'passed'
    assert (result['total'], result['passed']) == (2, 2)
    # A real zero-test unittest run exits zero, but must not be shown as passed.
    argv = runner_command('unittest', ())
    process = subprocess.run((sys.executable, *argv[1:]), cwd=tmp_path,
                             capture_output=True, text=True, check=False)
    result = report(argv, 'fixture', process.returncode, process.stdout, process.stderr)
    assert process.returncode == 0
    assert result['total'] == 0
    assert result['status'] != 'passed'


def test_all_skipped_is_not_reported_as_passed():
    result = report(('python',), 'fixture', 0,
        'SPROUT_TEST_RESULT={"total":2,"passed":0,"failed":0,"skipped":2}\n', '')
    assert result['status'] == 'skipped'
    mixed = report(('python',), 'fixture', 0,
        'SPROUT_TEST_RESULT={"total":2,"passed":1,"failed":0,"skipped":1}\n', '')
    assert mixed['status'] == 'passed'


def test_real_runners_all_skipped_are_not_passed(tmp_path):
    import subprocess
    import sys

    from Sprout.runtime.isolated_test_run import runner_command

    samples = {
        'unittest': 'import unittest\n@unittest.skip("fixture")\n'
                    'class Checks(unittest.TestCase):\n def test_case(self): pass\n',
        'pytest': 'import pytest\n@pytest.mark.skip(reason="fixture")\n'
                  'def test_case(): pass\n',
    }
    for framework, text in samples.items():
        (tmp_path / 'test_skip.py').write_text(text)
        argv = runner_command(framework, ('test_skip.py',))
        process = subprocess.run((sys.executable, *argv[1:]), cwd=tmp_path,
                                 capture_output=True, text=True, check=False)
        result = report(argv, 'fixture', process.returncode, process.stdout, process.stderr)
        assert process.returncode == 0
        assert (result['total'], result['passed'], result['skipped']) == (1, 0, 1)
        assert result['status'] == 'skipped'


async def test_testonly_scope_has_no_write_but_coding_keeps_write(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.cli.filesystem_task import prepare_filesystem
    from Sprout.gateway.identity import Principal
    from Sprout.orchestration.terminal import LocalOrchestratorBackend
    from Sprout.task.models import Task
    from Sprout.workspace.models import Workspace

    actor = Principal('owner', authenticated=True, source='cli')
    workspace = Workspace('workspace', tmp_path)
    session = SimpleNamespace(id='session')
    monkeypatch.setattr('Sprout.cli.filesystem_preparation.preflight',
                        AsyncMock(return_value='sha256:' + 'a' * 64))
    planner = AsyncMock()
    monkeypatch.setattr('Sprout.cli.filesystem_preparation.discover_plan', planner)
    monkeypatch.setattr('Sprout.security.local_diagnostics.current', lambda _: object())

    case = {"id": "case", "target": "app:echo", "expected_output": 1}
    specs = {"acceptance": {"cases": [case]}, "regression": {"cases": [case]}}
    monkeypatch.setenv("SPROUT_FILESYSTEM_VERIFICATION_SPEC", json.dumps(specs))
    for test_only in (True, False):
        planner.return_value = {'reads': ('app.py', 'test_app.py'),
            'writes': () if test_only else ('app.py',), 'execution': ('run_tests',)}
        runtime = SimpleNamespace(_orchestrator_backend=LocalOrchestratorBackend(None),
            _sessions=SimpleNamespace(require_owner=AsyncMock(return_value=session),
                                      frozen_principal=lambda _: actor),
            session_workspace_id=lambda _: workspace.id,
            get_workspace=AsyncMock(return_value=workspace),
            create_task=AsyncMock(return_value=Task(workspace_id=workspace.id, actor=actor)),
            _metadata_or_raise=lambda: SimpleNamespace(save_task=AsyncMock()),
            _cli_code_writes={}, _sandbox_lifecycle=SimpleNamespace(filesystem={}))
        task = await prepare_filesystem(runtime, workspace, SimpleNamespace(content='run'),
                                        session, actor, test_only=test_only)
        scope = runtime.create_task.call_args.kwargs['delegation_scope']
        assert ('file.write' in scope.allowed_actions) is (not test_only)
        assert ('file.apply' in scope.allowed_actions) is (not test_only)
        assert scope.allowed_paths == ('.',)
        assert runtime._cli_code_writes[task.id][3] == (() if test_only else ('app.py',))
        runtime._sandbox_lifecycle.filesystem[task.id].evidence.rmdir()
