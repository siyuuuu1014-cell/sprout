"""Real Runtime/SQLite/REPL; controlled terminal, model and native-open adapter."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from Sprout.cli.commands import chat
from Sprout.message.models import Message
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b4_cli_analysis import cli as cli
from Sprout.tests.test_r04_b4_cli_auto import automatic as automatic
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


async def test_project_request_defaults_to_root(automatic):
    cli = automatic
    cli.state.inputs = ["分析这个项目的结构和依赖，指出风险点"]
    cli.state.answers = [True]
    await cli.run()
    assert cli.create.call_args.kwargs["metadata"]["analysis_directory"] == "."
    assert len(cli.state.prompts) == 1
    assert any("Indexed counts" in s and "Files: 3" in s for s in cli.state.display)
    assert len([m for m in cli.state.seen if str(m.role) == "tool"]) == 1


@pytest.mark.parametrize("text,directory", [
    ("分析这个项目的架构", "."),
    ("analyze this project's architecture", "."),
    ("分析目录 src", "src"),
    ("分析 src 子目录", "src"),
])
async def test_analysis_forms_do_not_require_old_keywords(automatic, text, directory):
    cli = automatic
    cli.state.inputs = [text]
    cli.state.answers = [True]
    await cli.run()
    assert cli.create.call_args.kwargs["metadata"]["analysis_directory"] == directory
    assert len(cli.state.prompts) == 1


async def test_explicit_file_beats_project_words(cli):
    cli.state.inputs = ["分析 src/main.py 的项目结构和依赖"]
    cli.state.answers = [True]
    await cli.run()
    assert cli.create.call_args.kwargs["metadata"]["required_paths"] == ["src/main.py"]
    assert cli.captured.opens == ["src/main.py"]
    assert len(cli.state.prompts) == 1


async def test_plain_explicit_file_request_reaches_analysis(cli):
    cli.state.inputs = ["分析 src/main.py"]
    cli.state.answers = [True]
    await cli.run()
    assert cli.create.call_args.kwargs["metadata"]["required_paths"] == ["src/main.py"]
    assert cli.captured.opens == ["src/main.py"]


@pytest.mark.parametrize("text,selection", [
    ("分析 src/main.py的依赖", ("file", "src/main.py")),
    ("分析目录 src", ("directory", "src")),
    ("分析 src/", ("directory", "src/")),
    ("分析 ../outside.py 的项目结构", ("file", "../outside.py")),
    ("分析 src/main.py 和 src/worker.py", None),
])
def test_explicit_selection_never_expands_to_project(text, selection):
    assert chat._analysis_selection(text) == selection


async def test_explicit_directory_beats_project_words(automatic):
    cli = automatic
    cli.state.inputs = ["分析 src 子目录的项目结构"]
    cli.state.answers = [True]
    await cli.run()
    assert cli.create.call_args.kwargs["metadata"]["analysis_directory"] == "src"
    assert set(cli.captured.opens) == {"src/main.py", "src/worker.py"}


async def test_session_binding_survives_new_cli_and_cwd(cli, monkeypatch, tmp_path):
    runtime = cli.rig.runtime
    confirm = AsyncMock(return_value=cli.rig.workspace.id)
    monkeypatch.setattr(chat, "_confirm_cli_workspace", confirm)
    session_id, workspace_id = await chat._prepare_cli_context(runtime, None, "display")
    stored = await runtime._session_store.get_session(session_id)
    assert runtime.session_workspace_id(stored) == workspace_id
    monkeypatch.chdir(tmp_path)
    confirm.side_effect = AssertionError("must restore, not ask CWD again")
    assert await chat._prepare_cli_context(runtime, session_id, "display") == (
        session_id, workspace_id)
    assert confirm.await_count == 1


async def test_diagnostic_session_binds_current_directory_without_prompt(tmp_path, monkeypatch):
    from Sprout.config.settings import Settings
    from Sprout.gateway.identity import Principal
    from Sprout.security.local_diagnostics import LocalCLIDiagnostics
    from Sprout.storage.bundle import StorageBundle
    from Sprout.storage.local.sqlite.metadata import open_metadata_store
    from Sprout.tests.conftest import build_runtime

    storage = StorageBundle.in_memory()
    storage.metadata = open_metadata_store(str(tmp_path / 'metadata.db'))
    runtime = build_runtime(storage=storage)[0]
    principal = Principal("owner", source="cli", authenticated=True)
    monkeypatch.setattr(chat, "current_cli_principal", lambda: principal)
    mode = LocalCLIDiagnostics(Settings())
    monkeypatch.setattr(runtime, "_local_cli_diagnostics", mode, raising=False)
    monkeypatch.chdir(tmp_path)
    confirm = AsyncMock(side_effect=AssertionError("diagnostic launch must not prompt"))
    monkeypatch.setattr(chat, "_confirm_cli_workspace", confirm)
    try:
        async with mode.foreground(runtime):
            session_id, workspace_id = await chat._prepare_cli_context(runtime, None, "display")
            workspace = await runtime.get_workspace(workspace_id)
            assert workspace.root == tmp_path
            stored = await runtime._session_store.get_session(session_id)
            assert runtime.session_workspace_id(stored) == workspace_id
            assert await chat._prepare_cli_context(runtime, session_id, "display") == (
                session_id, workspace_id)
        confirm.assert_not_awaited()
    finally:
        await runtime.stop()
        await runtime.storage.close()


async def test_two_sessions_do_not_share_binding(cli, monkeypatch):
    runtime = cli.rig.runtime
    confirm = AsyncMock(side_effect=[cli.rig.workspace.id, ""])
    monkeypatch.setattr(chat, "_confirm_cli_workspace", confirm)
    first, workspace = await chat._prepare_cli_context(runtime, None, "display")
    second, other = await chat._prepare_cli_context(runtime, None, "display")
    assert first != second and workspace == cli.rig.workspace.id and other == ""
    assert runtime.session_workspace_id(await runtime._session_store.get_session(second)) == ""


async def test_missing_bound_workspace_never_falls_back(cli, monkeypatch):
    runtime = cli.rig.runtime
    await runtime._remember_session_workspace(cli.rig.session.id, "missing-workspace")
    confirm = AsyncMock(side_effect=AssertionError("no silent replacement"))
    monkeypatch.setattr(chat, "_confirm_cli_workspace", confirm)
    with pytest.raises(PermissionError, match="cli_session_workspace_unavailable"):
        await chat._prepare_cli_context(runtime, cli.rig.session.id, "display")
    confirm.assert_not_called()


async def test_cancel_then_two_more_turns_keep_session(cli):
    cli.state.inputs = ["分析 src/main.py 的依赖", "你好", "继续"]
    cli.state.intents = ["workspace", "conversation", "conversation"]
    cli.state.answers = [False]
    await cli.run()
    await asyncio.gather(*tuple(cli.rig.runtime._background_tasks))
    turns = await cli.rig.runtime._session_store.recent_turns(cli.rig.session.id, limit=20)
    assert len(turns) == 6
    assert [turn.content for turn in turns if str(turn.role) == "user"] == [
        "分析 src/main.py 的依赖", "你好", "继续"]
    assert cli.state.classification == 3
    assert "project_analysis_cancelled" in cli.state.display
    assert "CLI_PROJECT_MAIN_RESULT" in cli.state.display
    cli.create.assert_not_called()
    assert not cli.captured.opens


async def test_workspace_binding_requires_session_owner(cli, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(chat, "current_cli_principal", lambda: replace(
        cli.rig.principal, user_id="other"))
    with pytest.raises(PermissionError):
        await chat._prepare_cli_context(cli.rig.runtime, cli.rig.session.id, "display")


async def test_project_denied_does_not_prompt_for_files(automatic):
    from Sprout.security.access import AccessDecision
    from Sprout.security.layered_policy import LayeredPolicyEngine, PolicyLayer, PolicyRule

    cli = automatic
    # No configured ALLOW now means "ask", not an operator's explicit DENY.
    cli.rig.runtime._policy_engine = LayeredPolicyEngine(workspace=PolicyLayer('operator', (
        PolicyRule(action='directory.list', decision=AccessDecision.DENY, id='deny-project'),)))
    cli.rig.runtime._file_broker._policy = cli.rig.runtime._policy_engine
    cli.state.inputs = ["分析这个项目的结构和依赖，指出风险点"]
    cli.state.answers = [True]
    await cli.run()
    cli.create.assert_not_called()
    assert not cli.state.prompts
    assert any('directory.list denied' in s for s in cli.state.display)
    assert not cli.captured.opens and not await cli.rig.runtime.approvals.pending()


async def test_bound_context_does_not_grant_directory_access(cli, monkeypatch):
    monkeypatch.setattr(chat, "_confirm_cli_workspace", AsyncMock(
        return_value=cli.rig.workspace.id))
    sid, wid = await chat._prepare_cli_context(cli.rig.runtime, None, "display")
    session = await cli.rig.runtime._session_store.get_session(sid)
    assert session.user_id == cli.rig.principal.user_id
    result = await cli.rig.runtime.handle(Message("请分析项目结构", session_id=sid),
                                          principal=cli.rig.principal)
    assert result.metadata["reason"] == "authorized_analysis_task_required"
    assert wid == cli.rig.workspace.id and not cli.captured.opens


async def test_binding_survives_actual_runtime_and_sqlite_reopen(tmp_path, monkeypatch):
    from Sprout.gateway.identity import Principal
    from Sprout.rootstock.backends.sqlite_store import open_session_store
    from Sprout.storage.bundle import StorageBundle
    from Sprout.storage.local.sqlite.metadata import open_metadata_store
    from Sprout.tests.conftest import build_runtime

    root = tmp_path / "project"
    root.mkdir()
    principal = Principal("owner", source="cli", authenticated=True)
    monkeypatch.setattr(chat, "current_cli_principal", lambda: principal)

    def open_runtime():
        storage = StorageBundle.in_memory()
        storage.sessions = open_session_store(str(tmp_path / "sessions.db"))
        storage.metadata = open_metadata_store(str(tmp_path / "metadata.db"))
        return build_runtime(storage=storage)[0]

    runtime = open_runtime()
    workspace = await runtime.open_workspace(str(root))
    confirm = AsyncMock(return_value=workspace.id)
    monkeypatch.setattr(chat, "_confirm_cli_workspace", confirm)
    sid, wid = await chat._prepare_cli_context(runtime, None, "display")
    await runtime.stop()
    await runtime.storage.close()
    restarted = open_runtime()
    try:
        confirm.side_effect = AssertionError("persisted binding must win")
        monkeypatch.chdir(tmp_path)
        assert await chat._prepare_cli_context(restarted, sid, "display") == (sid, wid)
        root.rmdir()
        with pytest.raises(PermissionError, match="cli_session_workspace_unavailable"):
            await chat._prepare_cli_context(restarted, sid, "display")
    finally:
        await restarted.stop()
        await restarted.storage.close()


@pytest.mark.parametrize("single", [False, True])
async def test_chat_entry_single_shot_and_repl_lifecycle(cli, monkeypatch, single):
    from types import SimpleNamespace

    cli.state.analysis = False
    cli.state.inputs = ["你好", "继续", "谢谢"]
    monkeypatch.setattr(chat, "_confirm_cli_workspace", AsyncMock(
        return_value=cli.rig.workspace.id))
    monkeypatch.setattr("Sprout.gateway.local_identity.current_cli_principal",
                        lambda: cli.rig.principal)
    settings = SimpleNamespace(
        mcp=SimpleNamespace(clients={}), evolution=SimpleNamespace(enabled=False),
        model=SimpleNamespace(provider="controlled", model="stub"), web=SimpleNamespace())
    await chat._chat(cli.rig.runtime, settings, message="你好" if single else None,
                     session=None, user="display")
    assert cli.state.classification == (1 if single else 3)
    sessions = list(cli.rig.runtime._session_store._sessions.values())
    created = [s for s in sessions if s.id != cli.rig.session.id]
    assert len(created) == 1
    assert cli.rig.runtime.session_workspace_id(created[0]) == cli.rig.workspace.id
