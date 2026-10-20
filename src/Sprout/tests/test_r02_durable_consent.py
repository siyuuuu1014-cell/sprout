"""Temporary SQLite only: durable consent is a claim, not execution success."""
import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.gateway.identity import Principal
from Sprout.message.models import Message
from Sprout.rootstock.backends.sqlite_store import SCHEMA, SqliteSessionStore
from Sprout.session.manager import SessionManager
from Sprout.session.models import Turn
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.tests.test_r02_trusted_intent_audit import audit_runtime as audit_runtime
from Sprout.tests.test_workspace_consent import (
    CODING_REQUEST_TYPO,
    _AsksForWorkspace,
    _repo,
    _runtime_with_agent,
)


def guarded_runtime(path):
    runtime, _ = _runtime_with_agent(path, _AsksForWorkspace())
    runtime.submit_task = AsyncMock()
    runtime.start_worker = Mock()
    runtime.execute = AsyncMock()
    runtime.open_workspace = AsyncMock(wraps=runtime.open_workspace)
    runtime.create_task = AsyncMock(wraps=runtime.create_task)
    return runtime


async def ask(runtime, stream=False, text=CODING_REQUEST_TYPO):
    if stream:
        chunks = [c async for c in runtime.handle_stream(Message(text))]
        response = next(c.outbound for c in chunks if c.outbound)
        if runtime._background_tasks:
            await asyncio.gather(*list(runtime._background_tasks))
    else:
        response = await runtime.handle(Message(text), principal=Principal("u-1", source="cli"))
    assert response.metadata.get("workspace_consent_required") is True
    assert await runtime.list_tasks() == []
    return response.session_id


@pytest.mark.parametrize("stream", [False, True])
async def test_consumed_unknown_consent_cannot_be_replayed_after_restart(tmp_path, stream):
    repo = _repo(tmp_path)
    runtime = guarded_runtime(tmp_path)
    try:
        session = await ask(runtime, stream)
        await runtime.grant_workspace_consent(session, root=repo)
        assert len(await runtime.list_tasks()) == 1
        runtime.submit_task.assert_awaited_once()
        runtime.execute.assert_not_awaited()
    finally:
        await runtime.stop()
    reloaded = guarded_runtime(tmp_path)
    try:
        before = (await reloaded.get_session(session)).metadata.copy()
        with pytest.raises(LookupError):
            await reloaded.grant_workspace_consent(session, root=repo)
        assert len(await reloaded.list_tasks()) == 1
        assert (await reloaded.get_session(session)).metadata == before
        reloaded.open_workspace.assert_not_awaited()
        reloaded.create_task.assert_not_awaited()
        reloaded.submit_task.assert_not_awaited()
        reloaded.execute.assert_not_awaited()
    finally:
        await reloaded.stop()


@pytest.mark.parametrize("independent", [False, True])
async def test_concurrent_grants_have_one_durable_winner(tmp_path, independent):
    first = guarded_runtime(tmp_path)
    second = guarded_runtime(tmp_path) if independent else first
    try:
        session = await ask(first)
        if independent:
            # Bypass the driver's shared-instance registry only in this test.
            store = SqliteSessionStore(SqliteDatabase(tmp_path / "meta.db"))
            second._session_store = store
            second._sessions = SessionManager(store)
            assert first._session_store.database is not store.database
        repo = _repo(tmp_path)
        results = await asyncio.gather(
            first.grant_workspace_consent(session, root=repo),
            second.grant_workspace_consent(session, root=repo),
            return_exceptions=True,
        )
        assert sum(not isinstance(r, Exception) for r in results) == 1
        assert sum(isinstance(r, LookupError) for r in results) == 1
        assert len(await first.list_tasks()) == 1
        instances = [first, second] if independent else [first]
        assert sum(r.open_workspace.await_count for r in instances) == 1
        assert sum(r.create_task.await_count for r in instances) == 1
        assert sum(r.submit_task.await_count for r in instances) == 1
        for runtime in instances:
            runtime.execute.assert_not_awaited()
    finally:
        if independent:
            second._session_store.close()
            await second.stop()
        await first.stop()


@pytest.mark.parametrize("point", [
    "claim_before", "claim_after", "open", "create", "create_after", "submit",
])
async def test_failure_windows_never_replay_a_claim(tmp_path, point):
    repo = _repo(tmp_path)
    runtime = guarded_runtime(tmp_path)
    try:
        session = await ask(runtime)
        if point.startswith("claim_"):
            store = runtime._workspace_consent_store()
            original = store.claim_workspace_consent
            async def fail_claim(*args):
                if point == "claim_after":
                    assert await original(*args) is True
                raise OSError("simulated database outcome")
            store.claim_workspace_consent = fail_claim
        elif point == "create_after":
            original_create = runtime.create_task
            async def fail_after_create(*args, **kwargs):
                await original_create(*args, **kwargs)
                raise OSError("simulated crash after task persistence")
            runtime.create_task = AsyncMock(side_effect=fail_after_create)
        else:
            target = {"open": runtime.open_workspace, "create": runtime.create_task,
                      "submit": runtime.submit_task}[point]
            target.side_effect = OSError("simulated interrupted effect")
        with pytest.raises(OSError):
            await runtime.grant_workspace_consent(session, root=repo)
        if point.startswith("claim_"):
            runtime.open_workspace.assert_not_awaited()
            runtime.create_task.assert_not_awaited()
        count = len(await runtime.list_tasks())
        assert count == (1 if point in {"create_after", "submit"} else 0)
    finally:
        await runtime.stop()
    reloaded = guarded_runtime(tmp_path)
    try:
        if point == "claim_before":
            # Known pre-commit failure leaves the original pending eligibility.
            await reloaded.grant_workspace_consent(session, root=repo)
            assert len(await reloaded.list_tasks()) == 1
        else:
            with pytest.raises(LookupError):
                await reloaded.grant_workspace_consent(session, root=repo)
            assert len(await reloaded.list_tasks()) == count
            reloaded.open_workspace.assert_not_awaited()
            reloaded.create_task.assert_not_awaited()
            reloaded.submit_task.assert_not_awaited()
    finally:
        await reloaded.stop()


async def test_memory_backend_is_not_an_in_process_consent_fallback(audit_runtime):
    runtime, tasks, _ = audit_runtime
    first = await runtime.handle(
        Message("fix the bug"), principal=Principal("operator", source="cron"))
    history = await runtime.history(first.session_id)
    before = (await runtime.get_session(first.session_id)).metadata.copy()
    runtime.open_workspace = AsyncMock()
    runtime._remember_session_workspace = AsyncMock()
    with pytest.raises(LookupError, match="durable.*consent|consent.*durable"):
        await runtime.grant_workspace_consent(first.session_id,
                                            principal=Principal("operator", source="cli"))
    assert await runtime.history(first.session_id) == history
    assert (await runtime.get_session(first.session_id)).metadata == before
    assert tasks == [] and runtime._consumed_consents == set()
    runtime.open_workspace.assert_not_awaited()
    runtime._remember_session_workspace.assert_not_awaited()
    runtime.create_task.assert_not_awaited()
    runtime.submit_task.assert_not_awaited()
    runtime.execute.assert_not_awaited()


@pytest.mark.parametrize("mutation", ["owner", "source", "content", "envelope", "nonwrite"])
async def test_changed_authority_cannot_claim_original_pending(tmp_path, mutation):
    runtime = guarded_runtime(tmp_path)
    try:
        session = await ask(runtime)
        store = runtime._workspace_consent_store()
        held, _ = await runtime._held_workspace_consent(session)
        if mutation == "owner":
            with pytest.raises(PermissionError):
                await runtime.grant_workspace_consent(
                    session, principal=Principal("intruder", source="cli")
                )
        else:
            if mutation == "source":
                owned = await runtime.get_session(session)
                owned.metadata["entry_principal_v1"]["source"] = "cron"
                await store.save_session(owned)
            elif mutation in {"content", "nonwrite"}:
                content = ("不要修改文件，只读分析" if mutation == "nonwrite"
                           else "a changed instruction")
                await store.database.execute("UPDATE turns SET content=? WHERE id=?",
                                             (content, held.id))
            else:
                envelope = {**held.metadata, "user_id": "intruder"}
                await store.database.execute("UPDATE turns SET metadata_json=? WHERE id=?",
                                             (json.dumps(envelope), held.id))
            with pytest.raises((LookupError, ValueError)):
                await runtime.grant_workspace_consent(session)
        runtime.open_workspace.assert_not_awaited()
        runtime.create_task.assert_not_awaited()
        runtime.submit_task.assert_not_awaited()
        assert await runtime.list_tasks() == []
        row = await store.database.fetchone("SELECT state FROM workspace_consents_v1")
        assert row["state"] == "pending"
    finally:
        await runtime.stop()


async def test_legacy_hold_without_issuance_is_never_adopted(tmp_path):
    runtime = guarded_runtime(tmp_path)
    try:
        first = await runtime.handle(Message("hello"), principal=Principal("u-1", source="cli"))
        legacy = Turn(first.session_id, "user", CODING_REQUEST_TYPO, metadata={
            "message_id": "legacy", "channel": "cli", "user_id": "u-1",
            "message_metadata": {"workspace_consent_required": True,
                                 "proposed_workspace_root": "/not-opened"},
        })
        await runtime._session_store.append_turn(legacy)
        before = await runtime.history(first.session_id)
        with pytest.raises(LookupError):
            await runtime.grant_workspace_consent(first.session_id)
        assert await runtime.history(first.session_id) == before
        assert await runtime._workspace_consent_store().database.scalar(
            "SELECT count(*) FROM workspace_consents_v1 WHERE held_turn_id=?", (legacy.id,)) == 0
        runtime.open_workspace.assert_not_awaited()
        runtime.create_task.assert_not_awaited()
    finally:
        await runtime.stop()


@pytest.mark.parametrize("text", ["/task fix login", "修复登录问题", CODING_REQUEST_TYPO])
async def test_claim_is_permanent_and_cannot_be_reissued(tmp_path, text):
    runtime = guarded_runtime(tmp_path)
    try:
        session = await ask(runtime, text=text)
        store = runtime._workspace_consent_store()
        owned = await runtime.get_session(session)
        held, _ = await runtime._held_workspace_consent(session)
        await runtime.grant_workspace_consent(session, root=_repo(tmp_path))
        before = dict(await store.database.fetchone("SELECT * FROM workspace_consents_v1"))
        assert await store.issue_workspace_consent(owned, held) is False
        assert await store.claim_workspace_consent(owned, held, "second-owner") is False
        assert dict(await store.database.fetchone("SELECT * FROM workspace_consents_v1")) == before
        with pytest.raises(LookupError):
            await runtime.grant_workspace_consent(session)
        assert len(await runtime.list_tasks()) == 1
        assert runtime.open_workspace.await_count == runtime.create_task.await_count == 1
        assert runtime.submit_task.await_count == 1
        assert before["state"] == "claimed" and before["version"] == 1
        assert json.loads(before["snapshot_json"])["held_turn_id"] == held.id
    finally:
        await runtime.stop()


async def test_claim_cannot_use_a_caller_changed_snapshot(tmp_path):
    runtime = guarded_runtime(tmp_path)
    try:
        session = await ask(runtime)
        store = runtime._workspace_consent_store()
        owned = await runtime.get_session(session)
        held, _ = await runtime._held_workspace_consent(session)
        assert await store.claim_workspace_consent(
            owned, replace(held, content="forged"), "forged-owner") is False
        runtime.open_workspace.assert_not_awaited()
        runtime.create_task.assert_not_awaited()
    finally:
        await runtime.stop()


async def test_issue_failure_leaves_unclaimable_history(tmp_path):
    runtime = guarded_runtime(tmp_path)
    store = runtime._workspace_consent_store()
    store.issue_workspace_consent = AsyncMock(side_effect=OSError("issue interrupted"))
    try:
        with pytest.raises(OSError, match="issue interrupted"):
            await ask(runtime)
        runtime.open_workspace.assert_not_awaited()
        runtime.create_task.assert_not_awaited()
        sessions = await store.database.fetchall("SELECT id FROM sessions")
        session = sessions[0]["id"]
        assert await store.database.scalar("SELECT count(*) FROM workspace_consents_v1") == 0
    finally:
        await runtime.stop()
    reloaded = guarded_runtime(tmp_path)
    try:
        with pytest.raises(LookupError):
            await reloaded.grant_workspace_consent(session)
        reloaded.open_workspace.assert_not_awaited()
        reloaded.create_task.assert_not_awaited()
        reloaded.submit_task.assert_not_awaited()
    finally:
        await reloaded.stop()


async def test_memory_ordinary_conversation_and_owner_checks_remain_available(audit_runtime):
    runtime, tasks, _ = audit_runtime
    owner = Principal("operator", source="cli")
    first = await runtime.handle(Message("hello"), principal=owner)
    await runtime.handle(Message("hello again", session_id=first.session_id), principal=owner)
    history = await runtime.history(first.session_id, principal=owner)
    assert len(history) == 4 and tasks == []
    with pytest.raises(PermissionError):
        await runtime.handle(Message("hello", session_id=first.session_id),
                             principal=Principal("other", source="cli"))
    assert len(await runtime.history(first.session_id, principal=owner)) == 4


@pytest.mark.parametrize("source", ["cli", "cron", "web", "mcp_client"])
async def test_durable_snapshot_keeps_original_entry_source(tmp_path, source):
    runtime = guarded_runtime(tmp_path)
    try:
        response = await runtime.handle(Message("/task fix login"),
                                        principal=Principal("u-1", source=source))
        await runtime.grant_workspace_consent(response.session_id, root=_repo(tmp_path))
        tasks = await runtime.list_tasks()
        assert len(tasks) == 1 and tasks[0].actor.user_id == "u-1"
        assert tasks[0].actor.source == source
        runtime.execute.assert_not_awaited()
    finally:
        await runtime.stop()


async def test_additive_schema_preserves_old_rows_without_issuing_eligibility(tmp_path):
    db = SqliteDatabase(tmp_path / "old-session.db")
    db.executescript(SCHEMA)  # Pre-consent base schema; only a temporary database.
    principal = {"user_id": "owner", "source": "cli", "authenticated": False}
    await db.execute("INSERT INTO sessions(id,user_id,created_at,metadata_json) VALUES(?,?,?,?)",
                     ("session", "owner", "2026-01-01T00:00:00+00:00",
                      json.dumps({"entry_principal_v1": principal})))
    envelope = {"user_id": "owner", "channel": "cli", "message_id": "old-id",
                "message_metadata": {"workspace_consent_required": True}}
    await db.execute("INSERT INTO turns(id,session_id,role,content,created_at,seq,metadata_json) "
                     "VALUES(?,?,?,?,?,?,?)", ("held", "session", "user", "fix the bug",
                                              "2026-01-01T00:00:00+00:00", 1,
                                              json.dumps(envelope)))
    store = SqliteSessionStore(db)
    try:
        session = await store.get_session("session")
        held = (await store.recent_turns("session"))[0]
        assert held.content == "fix the bug" and held.metadata == envelope
        assert session.user_id == "owner" and session.metadata["entry_principal_v1"] == principal
        assert await store.claim_workspace_consent(session, held, "caller") is False
        assert await db.scalar("SELECT count(*) FROM workspace_consents_v1") == 0
    finally:
        store.close()
