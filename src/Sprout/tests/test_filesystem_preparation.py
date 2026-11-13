import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.cli.filesystem_preparation import plan_files, resolve_image
from Sprout.tests.test_cli_test_generation_continuity import (
    ORDER_TOTAL_REQUEST,
    PERSISTED_CODING_REQUESTS,
)
from Sprout.tests.test_r04_b4_cli_auto import automatic as automatic
from Sprout.tests.test_r04_b4_cli_auto import captured as captured
from Sprout.tests.test_r04_b4_cli_auto import cli as cli
from Sprout.tests.test_r04_b4_cli_auto import rig as rig


def _legacy_specs():
    case = {"id": "old_case", "target": "app:echo", "args": [1], "expected_output": 1}
    return json.dumps({"acceptance": {"cases": [case]}, "regression": {"cases": [case]}})


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


@pytest.mark.parametrize(("diagnostic", "has_tests"), [(False, True), (True, True), (True, False)])
async def test_foreground_preparation_never_prompts_for_paths_or_image(
    tmp_path, monkeypatch, capsys, diagnostic, has_tests,
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
        return_value={'reads': ('app.py', 'test_app.py') if has_tests else ('app.py',),
                      'writes': ('test_app.py',) if has_tests else ('app.py',),
                      'execution': ('run_tests',),
                      'actions': ('test_extension', 'test_execution')}))
    monkeypatch.setattr('Sprout.security.local_diagnostics.current',
                        lambda _: object() if diagnostic else None)
    prompt = AsyncMock(side_effect=AssertionError('No manual path/image prompts'))
    monkeypatch.setattr('questionary.text', prompt)
    monkeypatch.setattr('questionary.confirm', prompt)
    monkeypatch.setenv('SPROUT_FILESYSTEM_VERIFICATION_SPEC', _legacy_specs())
    message = SimpleNamespace(content='生成测试')
    if not has_tests:
        with pytest.raises(PermissionError, match="filesystem_test_plan_unavailable"):
            await prepare_filesystem(runtime, workspace, message, session, actor)
        runtime.create_task.assert_not_awaited()
        return
    result = await prepare_filesystem(runtime, workspace, message, session, actor)
    assert result.id == task.id
    plan = runtime.create_task.call_args.kwargs['metadata']['filesystem_plan_v1']
    assert plan['operations'] == [{'path': 'test_app.py', 'operation': 'update'}]
    assert plan['execution'] == ['run_tests']
    assert plan['actions'] == ['test_extension', 'test_execution']
    if diagnostic:
        assert plan["host_test"] == {
            "argv": ["python", "-m", "pytest", "-q"], "cwd": ".",
            "workspace_id": workspace.id, "image": "sha256:" + "a" * 64, "repair_attempts": 1,
        }
    else:
        assert "host_test" not in plan
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


def test_compound_data_task_keeps_analysis_and_coding():
    from Sprout.intents import detect_task_hint

    instruction = (
        "分析当前项目，完善数据处理功能，增加输入校验和异常处理，"
        "为修改涉及的函数补充单元测试。运行全部测试；如果发现失败，根据真实错误修复。"
        "完成独立验证和代码审查后，将结果直接写回项目，并给出修改总结。"
    )
    assert detect_task_hint(instruction)
    imports = {
        ("pipeline.py", "aggregation.py"), ("pipeline.py", "validation.py"),
        ("test_pipeline.py", "pipeline.py"), ("test_aggregation.py", "aggregation.py"),
    }
    paths = {"aggregation.py", "pipeline.py", "validation.py",
             "test_pipeline.py", "test_aggregation.py"}
    plan = plan_files(paths, instruction, imports=imports)
    assert set(plan["reads"]) == paths
    assert set(plan["writes"]) == paths
    assert plan["execution"] == ("run_tests",)

    assert "test_repair" in plan["actions"]

    limited = plan_files(paths, instruction + "不要修改 pipeline.py。", imports=imports)
    assert set(limited["reads"]) == paths
    assert set(limited["writes"]) == paths - {"pipeline.py"}

    with pytest.raises(PermissionError, match="ambiguous_source_target"):
        plan_files(paths | {"unrelated.py"}, instruction, imports=imports)
    assert not detect_task_hint("分析当前项目，如果发现失败，根据真实错误修复。")


def test_order_task_plans_pytest_and_keeps_spec_read_only():
    request = (
        "修复 order_total.py 中的 calculate_order_total 函数，"
        "严格按照 SPEC.md 实现折扣、税率、商品数据校验和异常处理，"
        "运行完整 pytest 测试，遇到失败自动修复并重新测试，"
        "验证通过后完成 Review、Apply 和写回后的只读复测。"
    )
    from Sprout.intents import detect_task_hint

    assert detect_task_hint(request)
    plan = plan_files(
        ("order_total.py", "SPEC.md", "README.md", "tests/test_order_total.py"), request,
    )
    assert "SPEC.md" in plan["reads"]
    assert plan["writes"] == ("order_total.py",)
    assert plan["execution"] == ("run_tests",)
    assert "test_repair" in plan["actions"]


def test_repair_against_spec_does_not_authorize_spec_write():
    plan = plan_files(
        ("order_total.py", "SPEC.md", "README.md"),
        "修复 order_total.py，按 SPEC.md 修复折扣，参考 README.md。",
    )
    assert plan["writes"] == ("order_total.py",)


def test_json_data_and_quoted_paths_do_not_change_operation_plan():
    text = ('修改 "app.py" 中的函数，并运行测试。'
            '参考 SPEC.md，输入示例为 {"path":"nonexistent.py","note":"不要修改源码"}。')
    plan = plan_files(("app.py", "test_app.py", "SPEC.md"), text)
    assert plan["writes"] == ("app.py",)
    assert plan["execution"] == ("run_tests",)


def test_readonly_reference_before_explicit_write_keeps_same_plan():
    plan = plan_files(
        ("app.py", "test_app.py", "SPEC.md"),
        '参考 SPEC.md，修改 "app.py" 中的函数，并运行测试。',
    )
    assert plan["writes"] == ("app.py",)
    assert plan["execution"] == ("run_tests",)


@pytest.mark.parametrize("instruction", PERSISTED_CODING_REQUESTS)
def test_persisted_multiline_coding_preserves_source_only_plan(instruction):
    paths = ("README.md", "SPEC.md", "order_total.py", "tests/test_order_total.py",
             "pyproject.toml")
    plan = plan_files(paths, instruction)
    assert plan["writes"] == ("order_total.py",)
    assert plan["execution"] == ("run_tests",)


def test_single_line_order_total_plan():
    paths = ("order_total.py", "SPEC.md", "tests/test_order_total.py")
    plan = plan_files(paths, ORDER_TOTAL_REQUEST)
    assert plan["writes"] == ("order_total.py",)
    assert set(plan["reads"]) == set(paths)
    assert plan["execution"] == ("run_tests",)


@pytest.mark.parametrize(("files", "target", "instruction"), [
    (("app.py", "test_app.py", "README.md"), "app.py",
     "修复 app.py，不修改 README.md。运行测试，写回后只读复测。"),
    (("src/auth.ts", "tests/auth.test.ts", "package.json"), "src/auth.ts",
     "修改 src/auth.ts，不修改 tests/。运行测试，完成审查、验收及写回后只读验证。"),
    (("cmd/server.go", "internal/auth.go", "README.md"), "cmd/server.go",
     "修复 cmd/server.go，不修改 internal/auth.go。运行测试，完成独立验证以及写回后只读复测。"),
])
@pytest.mark.parametrize("multiline", [False, True])
def test_coding_plan_qualifiers_across_projects(files, target, instruction, multiline):
    from Sprout.intents import WriteIntentDisposition, detect_task_hint, write_intent_disposition

    if multiline:
        instruction = instruction.replace("。运行", "。\n运行")
    assert write_intent_disposition(instruction) is WriteIntentDisposition.EXPLICIT_WRITE
    assert detect_task_hint(instruction)
    plan = plan_files(files, instruction)
    assert plan["writes"] == (target,)
    assert set(plan["reads"]) == set(files)
    assert plan["execution"] == ("run_tests",)



@pytest.mark.parametrize(("files", "target", "caches"), [
    (("README.md", "SPEC.md", "pyproject.toml", "src/app.py", "tests/test_app.py",
      "docs/usage.md", ".github/workflows/ci.yml"),
     "src/app.py", (".pytest_cache", "src/__pycache__", ".ruff_cache",
                    ".mypy_cache", ".venv", ".uv-cache")),
    (("package.json", "README.md", "src/app.ts", "tests/app.test.ts"),
     "src/app.ts", ("node_modules", "dist", ".cache", ".next", ".turbo")),
    (("go.mod", "README.md", "cmd/server.go", "internal/server_test.go"),
     "cmd/server.go", ("build", "dist", "internal/.CACHE")),
], ids=["python", "typescript", "go"])
async def test_discover_plan_prunes_generated_trees(automatic, monkeypatch, files, target, caches):
    import os

    from Sprout.cli.filesystem_preparation import discover_plan
    from Sprout.execution import file_access
    from Sprout.execution.file_snapshot import DirectoryEntry, DirectoryReadLimits, ResourceIdentity
    from Sprout.message.models import Message

    cli = automatic
    cli.state.answers = [True]
    cli.state.expected_prompt_classifications = 0
    runtime, root = cli.rig.runtime, cli.rig.root
    session = cli.rig.session
    principal = runtime._sessions.frozen_principal(session)
    await runtime._remember_session_workspace(session.id, cli.rig.workspace.id)

    # Only these fixture entries are exposed by the directory backend.
    generated = tuple(f"{cache}/README.md" for cache in caches)
    generated += tuple(f"{caches[0]}/extra-{i}.py" for i in range(40))
    records = {}
    for label in (*files, *generated):
        path = root / label
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if path.suffix == ".json" else "# fixture", encoding="utf8")
        while path != root:
            records.setdefault(path.parent, set()).add(path)
            path = path.parent

    visited = []
    async def listing(root, directory, limit, authorize):
        visited.append(directory.relative_to(root).as_posix())
        result = []
        for path in sorted(records[directory]):
            if not await authorize(path):
                continue
            is_dir = path.is_dir()
            if is_dir:
                identity = ResourceIdentity.from_stat(path.stat())
            else:
                with path.open("rb") as stream:
                    identity = ResourceIdentity.from_stat(os.fstat(stream.fileno()))
            result.append(DirectoryEntry(path.relative_to(root).as_posix(), is_dir, identity))
        return tuple(result)

    monkeypatch.setattr(file_access, "directory_entries", listing)
    enumerate_readable = runtime._file_broker.enumerate_readable

    async def bounded(*args, **kwargs):
        return await enumerate_readable(*args, **kwargs, limits=DirectoryReadLimits(max_entries=24))

    monkeypatch.setattr(runtime._file_broker, "enumerate_readable", bounded)
    result = await discover_plan(runtime, cli.rig.workspace,
        Message(f"修改 {target}", channel="cli", session_id=session.id,
                user_id=principal.user_id), session, principal)
    assert set(result["reads"]) == set(files)
    assert result["writes"] == (target,)
    assert {path.casefold() for path in cli.captured.opens} == {
        path.casefold() for path in files
    }
    assert len(cli.captured.opens) == len(files)
    assert set(visited) == {parent.as_posix() for label in files for parent in Path(label).parents}


@pytest.mark.parametrize("path", [
    ".pytest_cache/README.md", ".codex/README.md", ".env", "secrets/token.json",
])
def test_discovery_does_not_relax_sandbox_private_paths(tmp_path, path):
    from Sprout.sandbox.filesystem import checked_label

    with pytest.raises(PermissionError, match="filesystem_experiment_private_resource"):
        checked_label(path, tmp_path)



@pytest.mark.parametrize("backend", ["unbound-git", "filesystem", "bound-git"])
async def test_cli_coding_prepares_shared_plan(automatic, monkeypatch, backend):
    import shutil
    import subprocess
    from dataclasses import replace

    import questionary

    from Sprout.cli import filesystem_preparation
    from Sprout.cli.code_task import provider
    from Sprout.message.models import Message
    from Sprout.orchestration.terminal import LocalOrchestratorBackend
    from Sprout.workspace.models import WorkspaceKind

    cli = automatic
    runtime, workspace = cli.rig.runtime, cli.rig.workspace
    if backend != "filesystem":
        if backend == "unbound-git":
            (workspace.root / ".git").mkdir()
        else:
            git = shutil.which("git")
            if git is None:
                pytest.skip("Git executable is unavailable")
            for command in (("init", "-q"), ("add", "src", "src2"),
                            ("-c", "user.name=Sprout Test", "-c", "user.email=test@example.invalid",
                             "commit", "-qm", "Baseline")):
                subprocess.run([git, *command], cwd=workspace.root, check=True, capture_output=True)
        workspace = replace(workspace, kind=WorkspaceKind.GIT_REPOSITORY)
        store = cli.rig.storage.metadata
        await type(store).save_workspace(store, workspace)
    session, principal = cli.rig.session, cli.rig.principal
    await runtime._remember_session_workspace(session.id, workspace.id)
    runtime._orchestrator_backend = LocalOrchestratorBackend(None)
    instruction = "修改 src/main.py，并运行测试。"
    message = Message(instruction, channel="cli", session_id=session.id,
                      metadata={"workspace_id": workspace.id})
    monkeypatch.setenv("SPROUT_FILESYSTEM_VERIFICATION_SPEC", _legacy_specs())
    discover = AsyncMock(wraps=filesystem_preparation.discover_plan)
    monkeypatch.setattr(filesystem_preparation, "discover_plan", discover)
    preflight = AsyncMock(return_value="sha256:" + "a" * 64)
    monkeypatch.setattr(filesystem_preparation, "preflight", preflight)
    execute = AsyncMock(side_effect=AssertionError("Prepared Git Task executed"))
    submit = AsyncMock(side_effect=AssertionError("Prepared Git Task submitted"))
    monkeypatch.setattr(runtime, "execute", execute)
    monkeypatch.setattr(runtime, "submit_task", submit)

    def no_prompt(*args, **kwargs):
        raise AssertionError("Internal preparation prompt")

    monkeypatch.setattr(questionary, "text", no_prompt)
    monkeypatch.setattr(questionary, "confirm", no_prompt)
    prepare = provider(runtime, workspace.id)
    if backend != "filesystem":
        normal = {"id": "normal", "target": "src.main:main", "args": [],
                  "expected_output": 2, "category": "normal",
                  "evidence": {"path": "<requirement>", "quote": instruction}}
        boundary = {**normal, "id": "boundary", "category": "boundary"}
        regression = {**normal, "id": "retained"}
        invalid = {key: value for key, value in normal.items() if key != "expected_output"}
        invalid.update(id="invalid", category="invalid", expected_exception="ValueError")
        response = {"acceptance": {"cases": [normal, boundary, invalid]},
                    "regression": {"cases": [regression]}}
        chat = AsyncMock(return_value=SimpleNamespace(text=json.dumps(response)))
        model = SimpleNamespace(chat=chat)
        monkeypatch.setattr(runtime, "models", SimpleNamespace(default=lambda: model))
        if backend == "unbound-git":
            reply = await runtime._handle_task(
                message, session, principal=principal, foreground=True, code_task_provider=prepare,
            )
            assert reply.metadata["preparation_status"] == "awaiting_internal_acceptance"
            task = await runtime._metadata_or_raise().get_task(reply.metadata["task_id"])
        else:
            task = await prepare(message=message, session=session, principal=principal)
        assert task.status.value == "created"
        assert task.instruction == instruction
        assert "host_acceptance_v1" not in task.metadata
        from Sprout.execution.acceptance_producer import check_preparation

        bundle = task.metadata["git_acceptance_v1"]
        acceptance, regression = check_preparation(
            bundle, instruction, task.metadata["coding_preparation_v1"],
        )
        assert acceptance.version == 2
        assert regression.version == 1
        chat.assert_awaited_once()
        assert set(bundle["source_files"]) == set(task.metadata["required_paths"])
        assert bundle["generation_timestamp"] < task.created_at.isoformat()
        assert not await runtime._metadata_or_raise().list_execution_nodes(task.id)
        if backend == "unbound-git":
            stale = replace(task, metadata={})
            result = await type(runtime).execute(runtime, stale)
            assert result.error == "awaiting_internal_acceptance"
            assert (await runtime.resume_task(task)).error == "awaiting_internal_acceptance"
            with pytest.raises(ValueError, match="awaiting_internal_acceptance"):
                await runtime.submit_persisted_task(task.id)
            assert not await runtime._metadata_or_raise().list_execution_nodes(task.id)
            preflight.assert_not_awaited()
        else:
            from Sprout.execution.coding_command_tool import IsolatedCodingCommandTool
            from Sprout.execution.models import SandboxRef
            from Sprout.runtime.foreground_git_coding import ForegroundGitCoding

            assert task.metadata["coding_preparation_v1"]["state"] == "ready"
            assert task.metadata["host_acceptance_v2"]["bundle_sha256"] == bundle["bundle_sha256"]
            assert runtime._cli_code_writes[task.id] == (
                principal, session.id, workspace.id, ("src/main.py",),
            )
            scope = task.delegation_scope.bind_paths(str(workspace.root))
            assert scope.permits("file.read", str(workspace.root / "src/worker.py"))
            assert not scope.permits("file.read", str(workspace.root / "other.py"))
            with pytest.raises(PermissionError, match="cli_code_write_target_denied"):
                runtime._check_cli_write_target(task, "src/worker.py")
            assembly = ForegroundGitCoding(runtime, task, workspace)
            assert assembly.acceptance == acceptance
            assert assembly.regression == regression
            sandbox = SandboxRef(id="sandbox", kind="git_worktree", root=workspace.root)
            tool = IsolatedCodingCommandTool(
                assembly.commands, task=task, node=None, sandbox=sandbox,
                planned_paths=assembly.paths,
            )
            assert tool._guard.allows("src")
            assert not tool._guard.allows("unplanned")
            assert not tool._guard.allows(".git")
            preflight.assert_awaited_once()
    else:
        task = await prepare(message=message, session=session, principal=principal)
        experiment = runtime._sandbox_lifecycle.filesystem[task.id]
        assert experiment.task == task
        preflight.assert_awaited_once()

    discover.assert_awaited_once()
    plan = task.metadata["coding_preparation_v1"]
    assert set(plan["reads"]) == {"src/main.py", "src/worker.py", "src2/outside.py"}
    assert plan["writes"] == ["src/main.py"]
    assert plan["execution"] == ["run_tests"]
    assert set(plan["actions"]) == {"code_modification", "test_execution"}
    assert task.metadata["session_id"] == session.id
    assert task.workspace_id == workspace.id
    assert task.actor == principal
    assert task.execution_mode.value == "code"
    assert await runtime._metadata_or_raise().get_task(task.id) == task
    execute.assert_not_awaited()
    submit.assert_not_awaited()


@pytest.mark.parametrize("invalid", [False, True])
async def test_automatic_filesystem_acceptance_is_persisted_before_execution(
    automatic, monkeypatch, invalid,
):
    from Sprout.cli import filesystem_preparation
    from Sprout.cli.code_task import prepare
    from Sprout.execution import file_access
    from Sprout.execution.acceptance_producer import check_preparation, checked_bundle
    from Sprout.execution.file_snapshot import DirectoryEntry, ResourceIdentity
    from Sprout.message.models import Message
    from Sprout.orchestration.terminal import LocalOrchestratorBackend

    cli = automatic
    runtime, workspace = cli.rig.runtime, cli.rig.workspace
    session, principal = cli.rig.session, cli.rig.principal
    await runtime._remember_session_workspace(session.id, workspace.id)
    runtime._orchestrator_backend = LocalOrchestratorBackend(None)
    (workspace.root / "math_utils.py").write_text("def double(n): return n * 3\n", encoding="utf8")
    (workspace.root / "SPEC.md").write_text("Return twice the input.", encoding="utf8")
    (workspace.root / "README.md").write_text("Zero returns zero.", encoding="utf8")
    (workspace.root / "test_math_utils.py").write_text(
        "from math_utils import double\n\ndef test_zero(): assert double(0) == 0\n",
        encoding="utf8",
    )

    async def listing(root, directory, limit, authorize):
        rows = []
        for path in sorted(directory.glob("*")):
            if path.is_file() and await authorize(path):
                identity = ResourceIdentity.from_stat(path.stat())
                rows.append(DirectoryEntry(path.name, False, identity))
        return tuple(rows)

    monkeypatch.setattr(file_access, "directory_entries", listing)
    discover = AsyncMock(wraps=filesystem_preparation.discover_plan)
    monkeypatch.setattr(filesystem_preparation, "discover_plan", discover)
    monkeypatch.setattr(filesystem_preparation, "preflight",
                        AsyncMock(return_value="sha256:" + "a" * 64))
    monkeypatch.delenv("SPROUT_FILESYSTEM_VERIFICATION_SPEC", raising=False)

    def case(case_id, n, expected, category, path, quote):
        return {"id": case_id, "target": "math_utils:double", "args": [n],
                "expected_output": expected, "category": category,
                "evidence": {"path": path, "quote": quote}}

    reply = {"acceptance": {"cases": [
        case("positive", 3, 6, "normal", "SPEC.md", "Return twice the input."),
        case("zero", 0, 0, "boundary", "README.md", "Zero returns zero."),
    ]}, "regression": {"cases": [
        case("test_zero", 0, 0, "normal", "test_math_utils.py", "assert double(0) == 0"),
    ]}}
    chat = AsyncMock(return_value=SimpleNamespace(text="invalid" if invalid else json.dumps(reply)))
    model = SimpleNamespace(chat=chat)
    monkeypatch.setattr(runtime, "models", SimpleNamespace(default=lambda: model))
    instruction = "修复 math_utils.py 中的 double 函数，按 SPEC.md 返回两倍。"
    message = Message(instruction, channel="cli", session_id=session.id)
    if invalid:
        with pytest.raises(PermissionError, match="filesystem_acceptance_failed"):
            await prepare(
                runtime, workspace.id, message=message, session=session, principal=principal,
            )
        assert not runtime._sandbox_lifecycle.filesystem
        assert cli.create.await_count == 1  # Read-only analysis Task only.
        return
    task = await prepare(
        runtime, workspace.id, message=message, session=session, principal=principal,
    )
    stored = await runtime.get_task(task.id)
    bundle = stored.metadata["filesystem_acceptance_v1"]
    acceptance, regression = checked_bundle(bundle)
    assert acceptance.cases[0].expected_output == 6
    assert regression.cases[0].id == "test_zero"
    assert set(bundle["source_files"]) == set(stored.metadata["coding_preparation_v1"]["reads"])
    with pytest.raises(ValueError, match="acceptance_preparation_changed"):
        check_preparation(bundle, "Changed requirement", stored.metadata["coding_preparation_v1"])
    assert not await runtime.storage.metadata.list_execution_nodes(task.id)
    discover.assert_awaited_once()
    chat.assert_awaited_once()
    runtime._sandbox_lifecycle.filesystem[task.id].evidence.rmdir()



def test_document_reference_before_edit_routes_to_filesystem():
    from Sprout.intents import WriteIntentDisposition, detect_task_hint, write_intent_disposition

    instruction = (
        "按照 SPEC.md 修复 math_utils.py 中的 double 函数，让它正确返回输入的两倍，"
        "保留现有测试文件，并自动运行测试和完成写回。"
    )
    assert write_intent_disposition(instruction) is WriteIntentDisposition.EXPLICIT_WRITE
    assert detect_task_hint(instruction)
    assert not detect_task_hint("按照 SPEC.md 只分析 math_utils.py，不修改任何文件。")
    paths = ("math_utils.py", "SPEC.md", "tests/test_math_utils.py")
    plan = plan_files(paths, instruction)
    assert plan["writes"] == ("math_utils.py",)
    assert plan["execution"] == ("run_tests",)
