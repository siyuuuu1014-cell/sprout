"""Foreground Host planning over existing authorized analysis and Broker execution."""

import asyncio
import json
import os
import re
from dataclasses import replace
from pathlib import PurePosixPath

import typer

from Sprout.execution.coding_backend import _coding_capability, _run_coding_process
from Sprout.execution.coding_container import coding_image_ready
from Sprout.execution.docker_verification import validate_image
from Sprout.sandbox.filesystem import checked_label
from Sprout.security.approval import ApprovalManager
from Sprout.security.local_diagnostics import current, report
from Sprout.task.models import DelegationScope


def _test_file(path):
    name = PurePosixPath(path).name
    return (name.startswith('test_') or name.endswith('_test.py')
            or 'tests' in PurePosixPath(path).parts)


def plan_files(paths, instruction, *, symbols=None, imports=()):
    """Plan distinct effects; names and execution hints never grant authority."""
    from Sprout.intents import (
        WriteIntentDisposition,
        conditional_test_extension,
        detect_task_hint,
        project_clauses,
        project_feature_edit,
        write_intent_disposition,
    )

    paths = tuple(sorted(set(paths)))
    if not paths or len(paths) > 64:
        raise PermissionError('filesystem_planning_input_budget')
    result = {'reads': paths, 'writes': (), 'execution': (), 'actions': ()}
    from Sprout.runtime.isolated_test_run import execution_request

    if execution_request(instruction):
        return {**result, 'execution': ('run_tests',), 'actions': ('test_execution',)}
    if (not detect_task_hint(instruction)
            and write_intent_disposition(instruction) is WriteIntentDisposition.KNOWN_NONWRITE):
        return result
    sources = [p for p in paths if p.endswith('.py') and not _test_file(p)
               and PurePosixPath(p).name != '__init__.py']
    run_pattern = r'(?:运行|执行|\brun\b|\bexecute\b)[^，,。;；！？\n]*?(?:测试|\btests?\b)'
    run_tests = bool(re.search(run_pattern, instruction, re.I))
    # Execution is orthogonal to file writes. Remove only the execution clause
    # before classifying file operations, so "add logging then run tests" does
    # not become "add tests".
    body = re.sub(run_pattern, '', instruction, flags=re.I)
    if conditional_test_extension(instruction):
        body += "。补充缺失的测试"
    restricted = {
        path for clause in project_clauses(instruction)
        if (match := re.fullmatch(r"(?:不要修改|不修改)\s*([\w./-]+\.\w+)", clause))
        for path in paths if match[1] in {path, PurePosixPath(path).name}
    }
    writes, actions, edited_sources = [], [], []
    feature_sources = set()
    for clause in re.split(r'[，,、。;；\n]|并且|并|同时|然后|以及|最后', body):
        clause = re.sub(r"^\s*\d+[.、]\s*", "", clause).strip()
        if clause.startswith(("不要", "不修改", "不改动", "不写入", "保留")):
            continue
        mentioned = re.findall(r'[A-Za-z0-9_./-]+\.[A-Za-z][A-Za-z0-9_]*', clause)
        creating = bool(re.search(r'生成|创建|新增|添加|增加|编写|补写|补齐|'
                                  r'\b(?:generate|create|add|write)\b', clause, re.I))
        editing = bool(re.search(r'修改|编辑|修复|重构|调整|更新|补充|完善|'
                                 r'\b(?:modify|edit|fix|refactor|update|extend)\b', clause, re.I))
        extending = bool(re.search(r'补充|完善|修复失败的测试|\bextend\b', clause, re.I))
        test_change = (creating or extending) and bool(re.search(
            r'测试|覆盖率|覆盖|\btests?\b|test_[\w.-]+|[\w.-]+_test\.py', clause, re.I))
        selected = []
        for label in mentioned:
            matches = ([label] if label in paths else
                       [p for p in paths if '/' not in label and PurePosixPath(p).name == label])
            if len(matches) > 1:
                raise PermissionError('filesystem_planning_ambiguous_file')
            if not matches and (not creating or (test_change and not _test_file(label))):
                raise PermissionError('filesystem_planning_file_unavailable: ' + label)
            selected.append(matches[0] if matches else label)
        if test_change:
            test_targets = [p for p in selected if _test_file(p)]
            if not test_targets and feature_sources:
                related = {a for a, b in imports if b in feature_sources and _test_file(a)}
                test_targets = [p for p in paths if p in related]
            if not test_targets:
                targets = [p for p in selected if p in sources] or edited_sources or sources
                for path in targets:
                    name = 'test_' + PurePosixPath(path).stem + '.py'
                    matches = [p for p in paths if PurePosixPath(p).name == name]
                    if len(matches) > 1:
                        raise PermissionError('filesystem_planning_ambiguous_test_target')
                    test_targets.append(matches[0] if matches else
                                        ('tests/' + name if '/' in path else name))
            writes.extend(test_targets)
            actions.append('test_extension' if extending else 'test_generation')
        elif (creating and not selected
              and re.search(r"(?:新增|添加|实现)\s*[A-Za-z_]\w*\s*\(", clause)):
            if len(sources) != 1:
                raise PermissionError("filesystem_planning_ambiguous_source_target")
            writes.extend(sources)
            edited_sources.extend(sources)
            actions.append("code_modification")
        elif editing and not selected and not test_change:
            function = re.search(r"(?:修改|修复|重构)\s*([A-Za-z_]\w*)\s*函数", clause)
            if function and symbols is not None:
                targets = sorted({path for path, name in symbols if name == function[1]})
                if len(targets) != 1:
                    raise PermissionError("filesystem_planning_function_target_unresolved")
                writes.extend(targets)
                edited_sources.extend(targets)
                actions.append("code_modification")
            elif project_feature_edit(clause):
                # Without a named target, only a single connected source group
                # is unambiguous. Disconnected modules cannot enter write scope.
                group = set(sources[:1])
                for _ in sources:
                    group.update(b for a, b in imports if a in group and b in sources)
                    group.update(a for a, b in imports if b in group and a in sources)
                if not group or group != set(sources):
                    raise PermissionError("filesystem_planning_ambiguous_source_target")
                feature_sources = group
                writes.extend(p for p in sources if p not in restricted)
                edited_sources.extend(p for p in sources if p not in restricted)
                actions.append("code_modification")
            elif clause.startswith(("修改代码", "修改源码")) and len(sources) == 1:
                writes.extend(sources)
                edited_sources.extend(sources)
                actions.append("code_modification")
        elif selected and (editing or creating):
            symbol = re.search(r"\.(?:py)\s*中\s*(?:的\s*)?([A-Za-z_]\w*)\s*函数",
                               clause)
            if editing and not creating and symbol and symbols is not None:
                label = selected[0]
                name = symbol[1]
                if (label, name) not in symbols:
                    raise PermissionError(
                        f"filesystem_planning_function_not_indexed: {label}:{name}"
                    )
            writes.extend(selected)
            edited_sources.extend(p for p in selected if p in sources)
            if any(not _test_file(p) for p in selected):
                actions.append('code_modification')
            if any(_test_file(p) for p in selected):
                actions.append('test_extension' if editing else 'test_generation')
        elif creating and re.search(r'\breadme\b', clause, re.I):
            writes.append(next((p for p in paths if PurePosixPath(p).name.casefold()
                                == 'readme.md'), 'README.md'))
            actions.append('code_modification')
    if restricted.intersection(writes):
        raise PermissionError("filesystem_planning_conflicting_file_operations")
    if not writes and not run_tests:
        raise PermissionError('filesystem_planning_target_unresolved')
    if len(set(writes)) > 64:
        raise PermissionError('filesystem_planning_input_budget')
    if run_tests:
        actions.append('test_execution')
        clauses = project_clauses(instruction)
        if (any(clause.startswith("修复失败的测试") for clause in clauses)
                or any(a in {"如果发现失败", "如果测试失败"} and b == "根据真实错误修复"
                       for a, b in zip(clauses, clauses[1:], strict=False))):
            actions.append("test_repair")
    return {**result, 'writes': tuple(dict.fromkeys(writes)),
            'execution': ('run_tests',) if run_tests else (),
            'actions': tuple(dict.fromkeys(actions))}


async def discover_plan(runtime, workspace, message, owned, principal):
    # A fresh B1 publication supplies inventory and graph data. History is not authority.
    from Sprout.cli.commands.chat import _provide_cli_directory_task
    from Sprout.runtime.dialogue import project_answer

    report(runtime, 'Planning', 'analyzing project files')
    symbols, imports = set(), set()

    def capture(analysis):
        resources = {node.id: node.resource.path for node in analysis.graph.nodes
                     if node.resource is not None}
        imports.update((resources[edge.source], resources[edge.target])
                       for edge in analysis.graph.edges
                       if edge.relation in {"imports", "calls"}
                       and edge.source in resources and edge.target in resources)
        symbols.update(
            (node.resource.path, node.qualified_name)
            for node in analysis.graph.nodes
            if node.resource is not None and node.kind in {"function", "test"}
        )

    async def provider(**request):
        return await _provide_cli_directory_task(runtime, workspace, request['session'],
            request['principal'], request['message'], label='.', filesystem_preflight=True)

    response = await runtime._dispatch_project_analysis(
        replace(message, content='分析这个项目结构', metadata={}), owned,
        principal=principal, analysis_task=None, classification={"intent": "tool"},
        analysis_task_provider=provider, reply_to=message, on_capture=capture)
    if response is None or response.metadata.get('analysis_status') != 'ok':
        raise PermissionError('filesystem_planning_analysis_unavailable: ' +
                              (response.content if response else 'no publication'))
    fresh = await runtime._sessions.require_owner(owned.id, principal)
    if await project_answer(runtime, fresh, principal) is None:
        raise PermissionError('filesystem_planning_publication_unavailable')
    ref = fresh.metadata['project_analysis_ref_v1']
    source = await runtime._metadata_or_raise().get_node_result_source(ref['node_id'])
    paths = [item['path'] for item in json.loads(source.resources_json)]
    plan = plan_files(paths, message.content, symbols=symbols, imports=imports)
    overrides = runtime._file_broker._classify_overrides
    for group in (plan["reads"], plan["writes"]):
        for label in group:
            checked_label(label, workspace.root, overrides)
    report(runtime, 'Analysis', 'completed')
    from Sprout.intents import project_clauses
    from Sprout.runtime.isolated_test_run import execution_request

    clauses = project_clauses(message.content)
    if clauses and clauses[0].startswith(("分析", "请分析")) and execution_request(message.content):
        typer.echo(response.content)
    return plan


async def resolve_image(run):
    """Only inspect local images; unknown tags are never pulled implicitly."""
    engine = await run(('docker', 'version', '--format', '{{json .Server.Version}}'))
    if not engine.allowed or engine.exit_code != 0:
        raise PermissionError('docker_engine_unavailable')
    configured = os.getenv('SPROUT_FILESYSTEM_CODING_IMAGE', '').strip()
    if configured:
        validate_image(configured)
        candidates = [configured]
    else:
        images = await run(('docker', 'image', 'ls', '--filter',
                            'label=sprout.role=isolated-coding', '--no-trunc',
                            '--format', '{{.ID}}'))
        if not images.allowed or images.exit_code != 0:
            raise PermissionError('docker_image_discovery_denied')
        candidates = sorted(set(images.stdout.split()))[:16]
    for image in candidates:
        validate_image(image)
        inspection = await run(('docker', 'image', 'inspect', image))
        if coding_image_ready(inspection):
            return image
    raise PermissionError('docker_compatible_image_unavailable: provision a pinned '
                          'sprout.role=isolated-coding image with the project dependencies')


async def preflight(runtime, workspace, owned, principal):
    """Exact read-only Docker Host calls, no container creation or workspace execution."""
    owner = asyncio.current_task()
    scope = DelegationScope(allowed_paths=(str(workspace.root),),
                            allowed_actions=frozenset({'process.run'}))
    async def validate():
        fresh = await runtime._sessions.require_owner(owned.id, principal)
        if (asyncio.current_task() is not owner or not principal.authenticated
                or principal.source != 'cli'
                or runtime._sessions.frozen_principal(fresh) != principal
                or runtime.session_workspace_id(fresh) != workspace.id
                or await runtime.get_workspace(workspace.id) != workspace):
            raise PermissionError('filesystem_preflight_subject_changed')

    async def run(argv):
        await validate()
        async def check(manager, actor, resource, source):
            await validate()
            if (manager is not runtime.approvals or actor != principal
                    or resource != workspace.id or source != 'cli'):
                return False
            arguments = {'workspace_id': workspace.id, 'command': list(argv),
                         'purpose': 'filesystem-preparation-readonly'}
            mode = current(runtime)
            if mode is not None:
                await mode.require()
            record = await ApprovalManager.request(manager, 'filesystem.docker.inspect', arguments,
                session_id=owned.id, requested_by=principal.user_id, source='cli',
                resource_scope=workspace.id, ttl_seconds=60, request_approval=False)
            if mode is not None:
                mode.records.add(record.id)
            await manager.decide(record.id, True, principal=principal,
                                 decided_by=principal.user_id, channel='cli',
                                 reason='Foreground Filesystem Docker preflight')
            if mode is None:
                typer.echo('[Approval] AUTO-APPROVED: filesystem.docker.inspect')
            return await manager.is_approved('filesystem.docker.inspect', arguments,
                session_id=owned.id, requested_by=principal.user_id, source='cli',
                resource_scope=workspace.id)

        async def invoke(_, command, **kwargs):
            return await runtime._process_broker.run(workspace, command, cwd=workspace.root,
                scope=scope, actor=principal, session_id=owned.id, source='cli',
                requested_by=principal.user_id, resource_scope=workspace.id,
                timeout_seconds=15, max_output_bytes=64 * 1024)
        with _coding_capability(check):
            result = await _run_coding_process(invoke, '', argv)
        await validate()
        return result

    report(runtime, 'Docker', 'checking runtime')
    image = await resolve_image(run)
    typer.echo(f'[Docker] image resolved: {image}')
    return image


async def readonly_status(runtime, session_id, principal, prefix=''):
    """Observe owned records only; never approve gates or resume a task."""
    from uuid import NAMESPACE_URL, uuid5

    from Sprout.gateway.task_queries import TaskQueries

    owned = await runtime._sessions.require_owner(session_id, principal)
    if runtime._sessions.frozen_principal(owned) != principal:
        raise PermissionError('filesystem_status_subject_unavailable')
    mode = current(runtime)
    if mode is not None:
        await mode.require()
    tasks = [task for task in await runtime.list_tasks()
             if task.actor == principal and task.metadata.get('session_id') == owned.id
             and task.workspace_id == runtime.session_workspace_id(owned)
             and (not prefix or task.id.startswith(prefix))]
    if not tasks or (prefix and len(tasks) != 1):
        raise LookupError('filesystem_status_task_unavailable_or_ambiguous')
    rows = []
    for task in tasks:
        await TaskQueries(runtime)._owned_task(task.id, principal)
        nodes = await runtime._metadata_or_raise().list_execution_nodes(task.id)
        observations = []
        for node in nodes:
            output = node.metadata.get('output')
            output = output if isinstance(output, dict) else {}
            observations.append({'id': node.id, 'type': node.type.value,
                'status': node.status.value, 'reason': output.get('reason', ''),
                'error': node.metadata.get('error', ''),
                'verified': output.get('verified'), 'applied': output.get('applied'),
                'transaction_id': output.get('transaction_id')})
        coding = []
        reader = getattr(runtime._metadata_or_raise(), 'get_coding_execution', None)
        if reader is not None:
            for node in nodes:
                if node.type.value != 'agent':
                    continue
                identity = str(uuid5(NAMESPACE_URL, node.id + ':coding:evaluation-0'))
                record = await reader(identity)
                if record and (record.get('task_id'), record.get('node_id')) == (task.id, node.id):
                    result = record.get('result') or {}
                    coding.append({'node_id': node.id, 'execution_id': identity,
                        'state': record['state'], 'exit_code': result.get('exit_code'),
                        'started': result.get('started'), 'live_container_state': 'NOT_QUERIED'})
        rows.append({'task_id': task.id, 'status': task.status.value,
                     'nodes': observations, 'coding': coding, 'read_only': True})
    return json.dumps(rows, ensure_ascii=False, default=str, indent=2)
