"""Foreground execution of existing tests, never a write-intent override."""
import json
import re
from dataclasses import replace

from Sprout.execution.coding_backend import DockerCodingBackend, _coding_capability
from Sprout.execution.coding_session import CodingNodeSession
from Sprout.runtime.filesystem_experiment import FilesystemExperiment
from Sprout.security.approval import ApprovalStatus
from Sprout.security.local_diagnostics import report


def execution_request(text):
    from Sprout.intents import detect_task_hint, project_clauses

    text = text.strip().casefold()
    excluded = r"不要|别运行|不运行|不执行|取消|讨论|解释|如何|方案|是否|[？?]"
    if detect_task_hint(text) or re.search(excluded, text):
        return False
    clauses = tuple(c for c in project_clauses(text)
                    if not re.fullmatch(r"(?:不修改|不改动|不写入)(?:任何)?(?:项目)?文件", c))
    if not clauses:
        return False
    first = clauses[0]
    if first.startswith(("分析", "请分析")) and len(clauses) > 1:
        first = clauses[1]
    return bool(re.match(r"^(?:请|帮我)?\s*(?:只|仅)?(?:运行|执行).{0,180}测试", first)
                and not re.search(r"修改|生成|创建|补充|添加|编写", " ".join(clauses)))


def execution_report(command, image, exit_code, stdout, stderr):
    counts = dict(total=None, passed=None, failed=None, skipped=None)
    for line in stdout.splitlines():
        if line.startswith('SPROUT_TEST_RESULT='):
            try:
                value = json.loads(line.split('=', 1)[1])
                if all(type(value.get(k)) is int and value[k] >= 0 for k in counts):
                    counts = {k: value[k] for k in counts}
            except (ValueError, TypeError):
                pass
    status = 'unknown' if exit_code is None else 'failed'
    if exit_code == 0 and counts['total'] and counts['failed'] == 0:
        if counts['passed']:
            status = 'passed'
        elif counts['skipped'] == counts['total']:
            status = 'skipped'
    return {'command': list(command), 'image': image, 'exit_code': exit_code,
            'stdout': stdout, 'stderr': stderr, **counts, 'status': status}



def choose_framework(snapshots, files, available=None):
    """Inspect frozen configuration and AST without importing project code."""
    import ast
    import configparser
    import tomllib

    configured = False
    for path, snapshot in snapshots.items():
        name = path.rsplit('/', 1)[-1]
        if name not in {'pyproject.toml', 'pytest.ini', 'setup.cfg', 'tox.ini'}:
            continue
        try:
            text = snapshot.data.decode('utf-8-sig')
            if name == 'pyproject.toml':
                configured |= 'ini_options' in tomllib.loads(text).get('tool', {}).get('pytest', {})
            else:
                config = configparser.ConfigParser(interpolation=None)
                config.read_string(text)
                configured |= config.has_section('pytest') or config.has_section('tool:pytest')
        except (ValueError, UnicodeError, configparser.Error) as exc:
            raise PermissionError('test_framework_configuration_invalid') from exc
    pytest_style = False
    for path in files:
        tree = ast.parse(snapshots[path].data, filename=path)
        modules, bases = {'unittest'}, set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == 'unittest':
                        modules.add(alias.asname or alias.name)
                    if alias.name == 'pytest':
                        pytest_style = True
            elif isinstance(node, ast.ImportFrom):
                if node.module == 'unittest':
                    bases.update(alias.asname or alias.name for alias in node.names
                                 if alias.name == 'TestCase')
                if node.module and node.module.split('.')[0] == 'pytest':
                    pytest_style = True
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                pytest_style |= node.name.startswith('test_')
            if isinstance(node, ast.ClassDef) and node.name.startswith('Test'):
                unit = any((isinstance(base, ast.Name) and base.id in bases) or
                    (isinstance(base, ast.Attribute) and base.attr == 'TestCase'
                     and isinstance(base.value, ast.Name) and base.value.id in modules)
                    for base in node.bases)
                pytest_style |= not unit and any(isinstance(child, (ast.FunctionDef,
                    ast.AsyncFunctionDef)) and child.name.startswith('test_')
                    for child in node.body)
    framework = 'pytest' if configured or pytest_style else 'unittest'
    if available is not None and framework not in available:
        raise PermissionError(framework + '_runner_unavailable')
    return framework


def runner_command(framework, files):
    # Counts come from the real runner, not an LLM or a historical Task.
    if framework not in {'unittest', 'pytest'}:
        raise ValueError('Unsupported test framework')
    script = '''import json,sys,unittest
files=FILES
if FRAMEWORK == 'pytest':
 import importlib.util
 if importlib.util.find_spec('pytest') is None:
  sys.exit('pytest_runner_unavailable: selected image must provide pytest; no unittest fallback')
 import pytest
 class Counts:
  def __init__(self): self.results={}
  def pytest_runtest_logreport(self,report):
   if report.when == 'call' or report.failed or report.skipped:
    self.results[report.nodeid]=report.outcome
 c=Counts()
 code=pytest.main(['-q','-p','no:cacheprovider',*files],plugins=[c])
 values=list(c.results.values())
 counts=dict(total=len(values),passed=values.count('passed'),failed=values.count('failed'),skipped=values.count('skipped'))
else:
 import pathlib
 suite=unittest.TestSuite()
 for name in files:
  p=pathlib.Path(name)
  suite.addTests(unittest.defaultTestLoader.discover(str(p.parent),pattern=p.name,top_level_dir=str(p.parent)))
 r=unittest.TextTestRunner(verbosity=2).run(suite)
 failed=len(r.failures)+len(r.errors)+len(r.unexpectedSuccesses)
 counts=dict(total=r.testsRun,passed=r.testsRun-failed-len(r.skipped)-len(r.expectedFailures),failed=failed,skipped=len(r.skipped)+len(r.expectedFailures))
 code=0 if r.wasSuccessful() else 1
print('SPROUT_TEST_RESULT='+json.dumps(counts))
sys.exit(code)
'''.replace('FILES', repr(list(files))).replace('FRAMEWORK', repr(framework))
    return ('python', '-B', '-c', script)


class FilesystemTestRun(FilesystemExperiment):
    test_only = True

    async def verify(self, task, node):
        await self._subject(task, node)
        session = CodingNodeSession(self.runtime._metadata_or_raise(), self,
            self.runtime._file_broker, evidence=self.evidence, original=self.workspace.root,
            image=self.image, oracle_paths=('.git',), sandbox=self.sandbox.ref,
            task=task, node=node)
        context, _ = await session.prepare()
        framework = choose_framework(self.sandbox.sources, self.test_files)
        command = runner_command(framework, self.test_files)
        async def check(manager, actor, workspace, source):
            if (actor, workspace, source) != (task.actor, task.workspace_id, task.source):
                return False
            with coding_approval(manager, tool, _current_project_node()):
                grant = await self.approval(manager, task=task, node=node)
            return grant.status is ApprovalStatus.APPROVED
        from Sprout.execution.coding_command_tool import IsolatedCodingCommandTool
        from Sprout.security.local_diagnostics import coding_approval
        from Sprout.tools.context_keys import _current_project_node

        tool = IsolatedCodingCommandTool(self, task=task, node=node, sandbox=self.sandbox.ref,
                                        filesystem_root_only=True)
        report(self.runtime, 'Tests', 'started: ' + framework)
        with _coding_capability(check):
            process = await DockerCodingBackend(self.runtime._metadata_or_raise(), self,
                node_id=node.id, owner=node.metadata['execution_guard']['owner'],
                command_slot='tests').execute(replace(context, command=command,
                                                      read_only_input=True), node_id=node.id)
        result = execution_report(command, self.image, process.exit_code,
                                  process.stdout, process.stderr)
        result['framework'] = framework
        report(self.runtime, 'Tests', result['status'])
        return {'test_execution': result, 'status': result['status'], 'verified': False}


async def run_prepared(runtime, task, owned, principal):
    """Execute the exact registered read-only experiment and return its evidence."""
    registered = runtime._sandbox_lifecycle.filesystem.get(task.id)
    if (type(registered) is not FilesystemTestRun or registered.task != task
            or await runtime.get_task(task.id) != task or task.actor != principal
            or task.metadata.get('session_id') != owned.id):
        raise PermissionError('test_execution_subject_changed')
    report(runtime, 'Task', 'created: ' + task.id)
    result = await runtime.execute(task)
    node = await runtime._metadata_or_raise().get_execution_node(task.id + ':evaluation')
    output = (node.metadata.get('output') or {}) if node else {}
    proof = output.get('test_execution') if isinstance(output, dict) else None
    return {'task_id': task.id, 'task_status': result.status.value,
            'tests': proof, 'error': result.error}


async def dispatch(runtime, message, session, principal, foreground, provider):
    from Sprout.agent.base import AgentResult
    from Sprout.message.models import OutboundMessage

    content, metadata = 'test_execution_local_foreground_required', {'intent': 'tool'}
    if (foreground and provider is not None and principal is not None
            and principal.authenticated and principal.source == message.channel == 'cli'):
        owned = await runtime._sessions.require_owner(session.id, principal)
        if runtime._sessions.frozen_principal(owned) != principal:
            raise PermissionError('test_execution_subject_changed')
        try:
            task = await provider(message=replace(message, metadata={}),
                                  session=owned, principal=principal)
            if task is None:
                content = 'test_execution_cancelled'
            else:
                proof = await run_prepared(runtime, task, owned, principal)
                metadata.update(task_id=task.id, test_execution=proof["tests"])
                content = '```json\n' + json.dumps(
                    proof, ensure_ascii=False, indent=2) + '\n```'
        except PermissionError as exc:
            content = str(exc)
    await runtime._persist_turns(session, message, AgentResult(content=content, metadata=metadata))
    return OutboundMessage(content, message.channel, session.id, message.id, metadata)
