"""Host-native gateway identity, finite reads and publication integration.

Uses the real OS CLI identity and real file/SQLite/Guard implementations.
The offline echo model supplies conversation text, never trusted evidence.
"""
from dataclasses import replace

import pytest

from Sprout.config.loader import load_settings
from Sprout.gateway.local_identity import current_cli_principal
from Sprout.gateway.runtime_gateway import RuntimeGateway
from Sprout.gateway.task_adapter import GatewayRequest
from Sprout.runtime.factory import create_runtime
from Sprout.task.models import DelegationScope
from Sprout.tests.test_project_cli import _sandbox_config, _subject


async def _runtime(tmp_path):
    runtime = create_runtime(load_settings(str(_sandbox_config(tmp_path))))
    root = tmp_path / "demo"
    root.mkdir()
    (root / "service.py").write_text("def helper(): return 1\n", encoding="utf-8")
    (root / "app.py").write_text("from service import helper\n", encoding="utf-8")
    workspace = await runtime.open_workspace(root)
    return runtime, workspace


async def test_gateway_persists_real_subject_session_and_completed_sources(tmp_path):
    runtime, workspace = await _runtime(tmp_path)
    principal = current_cli_principal()
    try:
        response = await RuntimeGateway(runtime).execute(GatewayRequest(
            transport="local-host", caller=principal, workspace_id=workspace.id,
            instruction="explain selected code", execution_mode="read_only",
            delegation_scope=DelegationScope(
                allowed_paths=(str(workspace.root / "app.py"),),
                allowed_actions=frozenset({"tool.invoke", "file.read"}),
            ), metadata={"session_id": "untrusted-session-label"},
        ))
        assert response.status == "completed"
        task = await runtime.get_task(response.task_id)
        assert task.actor == principal and task.source == principal.source
        assert task.metadata["session_id"] != "untrusted-session-label"
        session = await runtime._sessions.require_owner(task.metadata["session_id"], principal)
        assert runtime._sessions.frozen_principal(session) == principal
        nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
        assert nodes
        for node in nodes:
            if node.status.value == "completed":
                source = await runtime.storage.metadata.get_node_result_source(node.id)
                assert source is not None
                assert source.owner == node.metadata["execution_guard"]["owner"]
                assert source.task_id == task.id
        assert not await runtime.pending_approvals()
    finally:
        await runtime.stop()


@pytest.mark.parametrize("mode", ["deferred", "copied", "mutated", "expired", "reused"])
async def test_wechat_background_handoff_requires_actual_claim(tmp_path, mode):
    import httpx

    from Sprout.gateway.channels.wechat_dialog import WeChatDialogClient, WeChatDialogGateway
    from Sprout.tests.test_wechat_dialog_gateway import _KEY, _callback_xml

    runtime, workspace = await _runtime(tmp_path)
    client = WeChatDialogClient(token="fixture", encoding_aes_key=_KEY, appid="wx-app",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"errcode": 0})))
    gateway = WeChatDialogGateway(runtime, client=client, default_workspace_id=workspace.id,
        scopes_by_workspace={workspace.id: DelegationScope(
            allowed_paths=(str(workspace.root / "app.py"),),
            allowed_actions=frozenset({"tool.invoke", "file.read"}))})
    if mode == "expired":
        gateway._dedup_window_seconds = 0
    try:
        ack, event = await gateway.ack_callback(
            {"encrypted": client.encrypt_message(_callback_xml())})
        assert ack["queued"] and event is not None
        if mode == "copied":
            event = replace(event)
        elif mode == "mutated":
            event.content = "changed after decrypt"
        if mode in {"copied", "mutated", "expired"}:
            with pytest.raises(PermissionError, match="wechat_event_source_required"):
                await gateway.process_event(event)
            assert not await runtime.storage.metadata.list_tasks()
        else:
            result = await gateway.process_event(event)
            task = await runtime.get_task(result["task_id"])
            assert task.status.value == "completed"
            assert task.actor.user_id == "user-1" and task.actor.source == "wechat_dialog"
            if mode == "reused":
                with pytest.raises(PermissionError, match="wechat_event_source_required"):
                    await gateway.process_event(event)
                assert len(await runtime.storage.metadata.list_tasks()) == 1
        assert not await runtime.pending_approvals()
    finally:
        await runtime.stop()


async def test_untrusted_gateway_metadata_cannot_create_authority(tmp_path, monkeypatch):
    runtime, workspace = await _runtime(tmp_path)
    before = {p.name: p.read_bytes() for p in workspace.root.iterdir()}
    opens = []

    def forbidden_open(*args, **kwargs):
        opens.append(True)
        raise AssertionError("Unprivileged metadata reached native file open")

    monkeypatch.setattr("Sprout.execution.file_broker.file_operation", forbidden_open)
    try:
        result = await RuntimeGateway(runtime).execute(GatewayRequest(
            transport="cli", workspace_id=workspace.id, instruction="explain code",
            execution_mode="read_only", metadata={"authenticated": True,
                "roles": ["admin"], "required_paths": ["app.py"]},
        ))
        assert result.status != "completed"
        task = await runtime.get_task(result.task_id)
        assert task.actor.authenticated is False and not task.actor.roles
        assert not opens
        for node in await runtime.storage.metadata.list_execution_nodes(task.id):
            assert await runtime.storage.metadata.get_node_result_source(node.id) is None
        assert not await runtime.pending_approvals()
        assert {p.name: p.read_bytes() for p in workspace.root.iterdir()} == before
    finally:
        await runtime.stop()


@pytest.mark.parametrize("mismatch", ["principal", "session", "workspace", "scope"])
async def test_analysis_mismatch_has_no_publication_or_approval(tmp_path, mismatch):
    from Sprout.gateway.project_gateway import ProjectGateway

    runtime, workspace = await _runtime(tmp_path)
    gateway = ProjectGateway(runtime, transport="cli", default_user="display-only")
    try:
        subject = await _subject(gateway, workspace.id)
        task = subject["analysis_task"]
        if mismatch == "principal":
            subject["principal"] = replace(subject["principal"], user_id="another-owner")
        elif mismatch == "session":
            task = replace(task, metadata={**task.metadata, "session_id": "not-owned"})
            await runtime.storage.metadata.save_task(task)
            subject["analysis_task"] = task
        elif mismatch == "workspace":
            other = tmp_path / "other"
            other.mkdir()
            workspace = await runtime.open_workspace(other)
        else:
            task = replace(task, delegation_scope=replace(task.delegation_scope,
                           denied_actions=frozenset({"file.read"})))
            await runtime.storage.metadata.save_task(task)
            subject["analysis_task"] = task
        with pytest.raises(PermissionError):
            await gateway.analyze_workspace(workspace.id, **subject)
        assert not await runtime.pending_approvals()
        for node in await runtime.storage.metadata.list_execution_nodes(task.id):
            assert await runtime.storage.metadata.get_node_result_source(node.id) is None
    finally:
        await runtime.stop()
