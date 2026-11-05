import asyncio

import pytest

from Sprout.cli.commands.chat import _approval_action_lines
from Sprout.events import APPROVAL_REQUESTED, EventBus
from Sprout.security.approval import ApprovalManager, ApprovalStatus
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.storage.local.sqlite.driver import SqliteDatabase
from Sprout.storage.local.sqlite.operational import SqliteOperationalStore


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_pending_requests_are_deduplicated(tmp_path, backend):
    path = tmp_path / "approvals.db"
    databases = [SqliteDatabase(path), SqliteDatabase(path)] if backend == "sqlite" else []
    try:
        stores = [SqliteOperationalStore(db) for db in databases] or [MemoryOperationalStore()]
        events = EventBus()
        requested = []
        events.subscribe(APPROVAL_REQUESTED, lambda event: requested.append(event))
        managers = [ApprovalManager(store, events=events) for store in stores]
        scope = {
            "task_id": "task", "session_id": "session", "requested_by": "operator",
            "source": "interactive", "resource_scope": "workspace-a/src",
        }
        arguments = {"command": "sprout", "args": ["info"]}
        records = await asyncio.gather(*(
            managers[i % len(managers)].request("cli_tool_run", arguments, **scope)
            for i in range(8)
        ))
        assert len({record.id for record in records}) == 1
        assert len(await stores[0].list_approvals(ApprovalStatus.PENDING)) == 1
        assert len(requested) == 1

        manager = managers[0]
        shortened = await managers[-1].request("cli_tool_run", arguments, ttl_seconds=30, **scope)
        assert shortened.id == records[0].id
        assert shortened.expires_at < records[0].expires_at
        longer = await manager.request("cli_tool_run", arguments, ttl_seconds=0, **scope)
        assert longer.id == shortened.id
        assert longer.expires_at == shortened.expires_at
        assert (await stores[0].get_approval(shortened.id)).expires_at == shortened.expires_at
        assert len(await stores[0].list_approvals(ApprovalStatus.PENDING)) == 1
        assert len(requested) == 1
        for field, value in {
            "task_id": "other-task", "session_id": "other-session",
            "requested_by": "other-operator", "source": "mcp_client",
            "resource_scope": "workspace-b/src",
        }.items():
            changed = await manager.request("cli_tool_run", arguments, **{**scope, field: value})
            assert changed.id != records[0].id
        assert len(await stores[0].list_approvals(ApprovalStatus.PENDING)) == 6

        await manager.decide(records[0].id, True)
        rejected = await manager.request("cli_tool_run", arguments, **scope)
        assert rejected.id != records[0].id
        await manager.decide(rejected.id, False)
        expired = await manager.request("cli_tool_run", arguments, ttl_seconds=-1, **scope)
        assert expired.id not in {records[0].id, rejected.id}
        await manager.sweep_expired()
        renewed = await manager.request("cli_tool_run", arguments, **scope)
        assert renewed.id not in {records[0].id, rejected.id, expired.id}
    finally:
        for db in databases:
            db.close()



@pytest.mark.asyncio
async def test_card_displays_only_its_own_command():
    args = {"command": "sprout", "args": ["info"]}
    record = await ApprovalManager(MemoryOperationalStore()).request("cli_tool_run", args)
    pending = [
        {"name": "cli_tool_run", "arguments": args},
        {"name": "cli_tool_run", "arguments": {"command": "sprout", "args": ["--help"]}},
    ]
    assert _approval_action_lines(record, pending) == ["sprout info"]
