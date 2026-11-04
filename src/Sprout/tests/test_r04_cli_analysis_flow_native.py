"""Default-policy CLI analysis: controlled model/input; real Runtime and native I/O."""
import json
import os
from dataclasses import asdict, replace

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from Sprout.cli.app import app
from Sprout.cli.commands import chat
from Sprout.llm.messages import LLMResponse
from Sprout.llm.registry import ModelRegistry
from Sprout.runtime import factory
from Sprout.runtime.intent_analysis import response_example
from Sprout.runtime.runtime import Runtime
from Sprout.security.access import AccessDecision, ActionRequest, ActionType
from Sprout.tools.executor import ToolExecutor
from Sprout.workspace.models import ResourceKind, ResourceRef

pytestmark = pytest.mark.skipif(
    os.name != 'nt' or os.environ.get('SPROUT_NATIVE_LOCAL_RECEIPT') != '1',
    reason='Explicit Windows native CLI analysis acceptance required')


def save(path, payload):
    path.write_text(json.dumps(payload, default=str, ensure_ascii=False, indent=2), 'utf-8')


@pytest.mark.parametrize('scenario', ['normal', 'cancel_then_continue', 'explicit_deny'])
def test_default_cli_analysis_has_real_effect_and_session_continuity(
        tmp_path, monkeypatch, scenario):
    project, home, data = (tmp_path / p for p in ('project', 'home', 'data'))
    for p in (project, home, data):
        p.mkdir()
    (project / 'src').mkdir()
    (project / 'src/app.py').write_text('def clamp_zero(value):\n    return value\n', 'utf-8')
    (project / 'pyproject.toml').write_text(
        '[project]\nname="analysis-fixture"\nversion="0.1.0"\n', 'utf-8')
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.delenv('TEMPORAL_HOST', raising=False)
    config = tmp_path / 'sprout.toml'
    lines = ['skills_dir=' + json.dumps(str(home / 'skills')), '[model]',
             'provider="aiyallm"', 'model="offline-controlled"', '[storage]']
    for field in ('operational', 'knowledge', 'metadata', 'session', 'context', 'usage'):
        lines.append(f'{field}="sqlite:///{(data / (field + ".db")).as_posix()}"')
    for field in ('blobs_dir', 'trajectory_dir', 'project_root'):
        lines.append(field + '=' + json.dumps(str(data / field)))
    lines += ['[storage.observations]', 'enabled=false', '[memory]', 'enabled=false',
              '[security.audit]', 'path=' + json.dumps(str(data / 'audit.jsonl'))]
    if scenario == 'explicit_deny':
        lines += ['[security.rules.workspace]',
                  json.dumps('directory.list:' + str(project)) + '="deny"']
    config.write_text('\n'.join(lines), 'utf-8')  # No test-specific directory ALLOW.
    monkeypatch.setenv('SPROUT_CONFIG', str(config))
    inputs, models, effects, snapshots, boundaries = [], [], [], [], []
    state = {'message': '', 'restored': False}

    class ControlledModel:
        name = 'controlled-native-analysis'

        async def chat(self, messages, **kwargs):
            text = '\n'.join(str(m.content or '') for m in messages)
            if "Classify the user's intent" in text:
                value = json.loads(response_example(True))
                intent = ('conversation' if '继续完成' in state['message'] else
                          'workspace' if '分析' in state['message'] else 'conversation')
                action = 'read_only_analysis' if intent == 'workspace' else 'conversation'
                value.update(intent=intent, confidence=0.99)
                value['semantic'].update(action_mode=action,
                    candidates=[{'intent': intent, 'action_mode': action}],
                    ambiguity_type='none', missing_information=[], needs_clarification=False,
                    question='')
                response = LLMResponse(content=json.dumps(value))
            elif any(m.role == 'tool' and m.name == 'project_analyze' for m in messages):
                assert 'clamp_zero' in text and 'Graph:' in text
                response = LLMResponse(content='REAL_ANALYSIS_RESULT: app.py defines clamp_zero.')
            else:
                if '刚才' in state['message'] or state['restored']:
                    assert 'REAL_ANALYSIS_RESULT' in text
                response = LLMResponse(content='CONTINUATION_RESULT: src/app.py is the core module')
            models.append({'messages': [asdict(m) for m in messages],
                           'response': asdict(response)})
            save(tmp_path / 'MODEL_CALLS.json', models)
            return response

    def registry(_settings):
        result = ModelRegistry()
        result.register(ControlledModel(), default=True)
        return result

    monkeypatch.setattr(factory, 'create_model_registry', registry)
    monkeypatch.setattr(factory, '_build_provider', lambda *a, **k: pytest.fail('online model'))
    execute = ToolExecutor.execute

    async def observed(executor, name, *args, **kwargs):
        if name == 'project_analyze':
            from Sprout.tools.context_keys import _current_project_node

            owner = _current_project_node()
            runtime = owner.runtime
            grant = runtime._cli_directory_tasks[owner.task.id]
            request = ActionRequest(task_id=owner.task.id, actor=owner.task.actor,
                action=ActionType.DIRECTORY_LIST, scope=grant['scope'],
                resource=ResourceRef(owner.workspace.id, grant['arguments']['path'],
                                     ResourceKind.OTHER))
            assert runtime._policy_engine is runtime._file_broker._policy
            assert runtime._policy_engine.decide(request).decision is AccessDecision.ALLOW
            invalid = {
                'actor': replace(request, actor=replace(request.actor, user_id='other')),
                'task': replace(request, task_id='not-issued'),
                'workspace': replace(request, resource=replace(request.resource,
                                                               workspace_id='other')),
                'scope': replace(request, scope=replace(request.scope,
                    allowed_paths=(str(owner.workspace.root.parent),))),
                'outside': replace(request, resource=replace(request.resource,
                    path=str(owner.workspace.root.parent / 'unapproved'))),
                'write': replace(request, action=ActionType.FILE_WRITE),
                'process': replace(request, action=ActionType.PROCESS_RUN),
                'apply': replace(request, action=ActionType.FILE_APPLY),
            }
            for boundary, attempt in invalid.items():
                decision = runtime._policy_engine.decide(attempt)
                assert decision.decision is not AccessDecision.ALLOW
                assert not any(r.startswith('directory:grant:cli-directory:')
                               for r in decision.matched_rules)
                boundaries.append({'boundary': boundary, 'decision': asdict(decision)})
            save(tmp_path / 'BOUNDARIES.json', boundaries)
        result = await execute(executor, name, *args, **kwargs)
        effects.append({'name': name, 'ok': result.ok, 'text': result.to_text()})
        save(tmp_path / 'TOOLS.json', effects)
        return result

    monkeypatch.setattr(ToolExecutor, 'execute', observed)
    stop = Runtime.stop

    async def stopping(runtime):
        tasks = await runtime.storage.metadata.list_tasks()
        nodes, sources = [], []
        for task in tasks:
            for node in await runtime.storage.metadata.list_execution_nodes(task.id):
                nodes.append(asdict(node))
                source = await runtime.storage.metadata.get_node_result_source(node.id)
                if source:
                    sources.append(asdict(source))
        sessions = [await runtime.get_session(sid) for sid in {
            t.metadata.get('session_id') for t in tasks if t.metadata.get('session_id')}]
        snapshots.append({'tasks': [asdict(t) for t in tasks], 'nodes': nodes,
            'sessions': [asdict(s) for s in sessions], 'sources': sources,
            'approvals': [asdict(a) for a in await runtime.approvals.store.list_approvals()]})
        save(tmp_path / 'SNAPSHOTS.json', snapshots)
        await stop(runtime)
        assert not runtime._cli_directory_tasks
        await runtime.storage.close()

    monkeypatch.setattr(Runtime, 'stop', stopping)
    monkeypatch.setattr(_NamedTextIOWrapper, 'isatty', lambda self: self.name == '<stdin>')
    confirmations = iter([False, True] if scenario == 'cancel_then_continue' else [True] * 5)

    class Answer:
        def __init__(self, value):
            self.value = value

        async def ask_async(self):
            return self.value

    def prompt(kind, message, **kwargs):
        value = 'current' if kind == 'select' else next(confirmations)
        inputs.append({'kind': kind, 'prompt': str(message), 'answer': value})
        return Answer(value)

    for kind in ('select', 'confirm'):
        monkeypatch.setattr(chat.questionary, kind,
                            lambda message, _kind=kind, **kw: prompt(_kind, message, **kw))
    messages = ['分析这个项目的入口和依赖']
    if scenario == 'normal':
        messages += ['继续解释刚才分析的核心模块', '结合刚才的分析，解释 src/app.py 的作用',
                     '重新分析当前项目的入口和依赖', '分析 src 子目录的依赖']
    elif scenario == 'cancel_then_continue':
        messages += ['继续完成刚才未完成的项目分析', '继续解释刚才分析的核心模块']
    messages += ['/exit']
    sequence = iter(messages)

    async def readline():
        state['message'] = next(sequence)
        return state['message']

    monkeypatch.setattr(chat, '_readline_with_slash_hint', readline)
    monkeypatch.chdir(project)
    invocation = [] if scenario == 'normal' else ['chat', '--config', str(config)]
    result = CliRunner().invoke(app, invocation)
    (tmp_path / 'CLI_FIRST.log').write_text(result.output, 'utf-8')
    save(tmp_path / 'CLI_RESULT.json', {'exit_code': result.exit_code,
         'argv': invocation, 'exception': repr(result.exception), 'inputs': inputs})
    assert result.exit_code == 0, (result.output, result.exception)
    assert (project / 'src/app.py').read_text('utf-8') == (
        'def clamp_zero(value):\n    return value\n')
    completed = [n for n in snapshots[0]['nodes'] if n['metadata'].get('output', {}).get(
        'analysis_ok') and n['status'] == 'completed']
    if scenario == 'explicit_deny':
        assert not completed and not effects and not snapshots[0]['approvals']
        assert 'denied' in result.output.lower() or '拒绝' in result.output
        return
    assert len(completed) == (3 if scenario == 'normal' else 1)
    assert len([e for e in effects if e['name'] == 'project_analyze' and e['ok']]) == len(completed)
    assert all(n['metadata']['execution_guard']['state'] == 'completed' for n in completed)
    assert len(snapshots[0]['sources']) >= len(completed)
    assert len([i for i in inputs if i['kind'] == 'confirm']) == 2
    approvals = snapshots[0]['approvals']
    assert len(approvals) == (2 if scenario == 'normal' else 1)
    assert all(a['tool'] == 'directory.list' and not a['single_use']
               and a['status'] == 'approved' for a in approvals)
    sid = snapshots[0]['sessions'][0]['id']
    state['restored'] = True
    restored_messages = ['结合刚才的分析，解释 src/app.py 的作用']
    if scenario == 'normal':
        restored_messages.append('重新分析 src 子目录的依赖')
    sequence = iter([*restored_messages, '/exit'])
    monkeypatch.chdir(home)
    restarted = CliRunner().invoke(app, ['chat', '--config', str(config), '--session', sid])
    (tmp_path / 'CLI_RESTART.log').write_text(restarted.output, 'utf-8')
    assert restarted.exit_code == 0, (restarted.output, restarted.exception)
    assert 'CONTINUATION_RESULT' in restarted.output
    assert snapshots[1]['sessions'][0]['id'] == sid
    assert snapshots[1]['sessions'][0]['metadata']['workspace_id'] == (
        snapshots[0]['sessions'][0]['metadata']['workspace_id'])
    # Only an explicit rescan executes again; restore/follow-ups do not scan.
    assert len(effects) == len(completed) + (1 if scenario == 'normal' else 0)
    assert len([i for i in inputs if i['kind'] == 'confirm']) == 2
    if scenario == 'normal':
        # A new Session on the same project must obtain its own approval.
        monkeypatch.chdir(project)
        state['restored'] = False
        sequence = iter(['分析这个项目的入口和依赖', '/exit'])
        fresh = CliRunner().invoke(app, [])
        (tmp_path / 'CLI_NEW_SESSION.log').write_text(fresh.output, 'utf-8')
        assert fresh.exit_code == 0, (fresh.output, fresh.exception)
        assert len([i for i in inputs if i['kind'] == 'confirm']) == 3
        grants = snapshots[-1]['approvals']
        assert len(grants) == 3 and len({g['session_id'] for g in grants}) == 2
        assert len(effects) == len(completed) + 2
    save(tmp_path / 'FINAL.json', {'session_id': sid, 'scenario': scenario,
         'actual_analysis_nodes': len(completed), 'confirmations': inputs,
         'first_exit': result.exit_code, 'restart_exit': restarted.exit_code})
