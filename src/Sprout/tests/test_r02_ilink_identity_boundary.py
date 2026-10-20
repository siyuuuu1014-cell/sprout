"""Scope blocker: real iLink conversation adapter, no network or user data.

This exercises the post-admission adapter path, not iLink authentication.
The existing L1 failures use an outdated Runtime stub; this probe separately
demonstrates missing Principal wiring with the actual R02 Runtime.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from Sprout.gateway.channels.weixin_ilink import (
    WeixinIlinkAccount,
    WeixinIlinkGateway,
    WeixinIlinkInbound,
)
from Sprout.tests.test_r02_trusted_intent_audit import audit_runtime as audit_runtime


async def polled(gateway, inbound):
    """Adapt old direct-call tests to a successful instance-client poll."""
    gateway._client.get_updates = AsyncMock(return_value={"ret": 0, "msgs": [{
        "message_type": 1, "from_user_id": inbound.from_user_id,
        "client_id": inbound.message_id, "context_token": inbound.context_token,
        "item_list": [{"type": 1, "text_item": {"text": inbound.text}}],
    }]})
    return (await gateway.poll_once())["results"][0]


async def test_ilink_same_peer_continuation_needs_trusted_adapter_identity(audit_runtime):
    runtime, tasks, _ = audit_runtime
    # Use the real adapter and in-memory state; no external client is created.
    gateway = WeixinIlinkGateway(
        runtime, client=SimpleNamespace(),
        account=WeixinIlinkAccount(token="test-only", account_id="test-bot"),
        allowed_users=("test-only-peer",),
    )
    gateway._send_reply = AsyncMock(return_value={"ok": True})
    first = await polled(gateway, WeixinIlinkInbound("test-only-peer", "hello", "one"))
    second = await polled(gateway, WeixinIlinkInbound("test-only-peer", "hello again", "two"))
    assert second["session_id"] == first["session_id"]
    assert tasks == []
    assert gateway._send_reply.await_count == 2
