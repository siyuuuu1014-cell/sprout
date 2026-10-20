"""iLink transport receipts, account namespace, and legacy ownership gates."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.cli.commands import gateway as cli_gateway
from Sprout.gateway.channels.weixin_ilink import (
    WeixinIlinkAccount,
    WeixinIlinkGateway,
    WeixinIlinkInbound,
    validate_account_id,
)
from Sprout.gateway.channels.weixin_ilink_state import WeixinIlinkStateStore
from Sprout.tests.test_r02_ilink_identity_boundary import polled
from Sprout.tests.test_r02_trusted_intent_audit import audit_runtime as audit_runtime


def gateway_for(runtime, account="bot-a", peer="peer"):
    gateway = WeixinIlinkGateway(
        runtime, client=SimpleNamespace(),
        account=WeixinIlinkAccount(token="test-only", account_id=account),
        allowed_users=(peer,),
    )
    gateway._send_reply = AsyncMock(return_value={"ok": True})
    return gateway


@pytest.mark.parametrize("account", ["", " ", "../a", "a/b", "a\\b", "a:b", "a.",
                                    "CON", "aux.json", "a\x00b"])
async def test_account_rejected_before_state_or_client(account, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("State/client construction must not happen")
    monkeypatch.setattr(cli_gateway, "WeixinIlinkStateStore", forbidden)
    monkeypatch.setattr(cli_gateway, "WeixinIlinkClient", forbidden)
    with pytest.raises(ValueError, match="account ID"):
        await cli_gateway._run_weixin_ilink(
            None, None, WeixinIlinkAccount(token="test-secret", account_id=account), tmp_path
        )


async def test_known_account_paths_isolate_all_peer_state(tmp_path, monkeypatch):
    stores = []
    class Stop(Exception):
        pass
    def store(path, **kwargs):
        stores.append((path, WeixinIlinkStateStore(path, **kwargs)))
        return stores[-1][1]
    def stop(*args, **kwargs):
        raise Stop
    monkeypatch.setattr(cli_gateway, "WeixinIlinkStateStore", store)
    monkeypatch.setattr(cli_gateway, "WeixinIlinkGateway", stop)
    settings = SimpleNamespace(weixin_ilink=SimpleNamespace(
        default_workspace_id="", dm_policy="closed", allowed_users=()
    ))
    for account in ("bot-a", "bot-b"):
        with pytest.raises(Stop):
            await cli_gateway._run_weixin_ilink(None, settings,
                WeixinIlinkAccount(token="test-only", account_id=account), tmp_path / "context")
    assert stores[0][0] == tmp_path / "state" / "bot-a.json"
    assert stores[1][0] == tmp_path / "state" / "bot-b.json"
    a, b = stores[0][1], stores[1][1]
    await a.set_session_id("peer", "a-session")
    await a.set_context_token("peer", "a-context")
    await a.set_last_task_id("peer", "a-task")
    assert await b.get_session_id("peer") == ""
    assert await b.get_context_token("peer") == ""
    assert await b.get_last_task_id("peer") == ""


async def test_direct_call_and_expired_poll_dto_cannot_supply_identity(audit_runtime):
    runtime, tasks, _ = audit_runtime
    gateway = gateway_for(runtime)
    dto = WeixinIlinkInbound("peer", "hello", "one", "self-declared-context")
    with pytest.raises(PermissionError, match="poll receipt"):
        await gateway.handle_inbound(dto)
    await polled(gateway, dto)
    assert gateway._poll_receipts == {}
    with pytest.raises(PermissionError):
        await gateway._handle_conversation(dto, "hello", "")
    assert tasks == []


async def test_account_and_sender_scope_and_foreign_session_denial(audit_runtime):
    runtime, tasks, _ = audit_runtime
    a, b = gateway_for(runtime, "bot-a"), gateway_for(runtime, "bot-b")
    one = await polled(a, WeixinIlinkInbound("peer", "hello", "one"))
    two = await polled(b, WeixinIlinkInbound("peer", "hello", "two"))
    owner_a = (await runtime.get_session(one["session_id"])).user_id
    owner_b = (await runtime.get_session(two["session_id"])).user_id
    assert owner_a != owner_b
    assert owner_a not in {"peer", "bot-a", "system"}
    c = gateway_for(runtime, "bot-a", peer="other-peer")
    three = await polled(c, WeixinIlinkInbound("other-peer", "hello", "four"))
    assert (await runtime.get_session(three["session_id"])).user_id not in {owner_a, owner_b}
    # Even a misbound locator cannot make account B read/append account A.
    await b._state_store.set_session_id("peer", one["session_id"])
    runtime._contexts.build = AsyncMock()
    before = await runtime.history(one["session_id"])
    with pytest.raises(PermissionError):
        await polled(b, WeixinIlinkInbound("peer", "hello", "three"))
    runtime._contexts.build.assert_not_awaited()
    assert len(await runtime.history(one["session_id"])) == len(before)
    assert tasks == []


async def test_unadmitted_sender_never_gets_receipt(audit_runtime):
    runtime, _, _ = audit_runtime
    gateway = gateway_for(runtime)
    result = await polled(gateway, WeixinIlinkInbound("stranger", "hello", "one"))
    assert result == {"ok": True, "ignored": "not_allowed"}
    assert gateway._poll_receipts == {}
    gateway._send_reply.assert_not_awaited()


async def test_legacy_sender_only_session_is_not_adopted(audit_runtime):
    runtime, _, _ = audit_runtime
    legacy = await runtime.create_session("peer")
    gateway = gateway_for(runtime)
    await gateway._state_store.set_session_id("peer", legacy.id)
    with pytest.raises(PermissionError):
        await polled(gateway, WeixinIlinkInbound("peer", "hello", "one"))
    assert (await runtime.get_session(legacy.id)).user_id == "peer"


def test_account_id_is_preserved_not_normalized():
    assert validate_account_id("bot-1@im.bot") == "bot-1@im.bot"


def test_shared_state_object_cannot_be_bound_to_two_accounts(audit_runtime):
    runtime, _, _ = audit_runtime
    state = WeixinIlinkStateStore()
    first = WeixinIlinkGateway(runtime, client=SimpleNamespace(), state_store=state,
                              account=WeixinIlinkAccount(token="test-only", account_id="bot-a"))
    assert first._state_store is state
    with pytest.raises(ValueError, match="account state"):
        WeixinIlinkGateway(runtime, client=SimpleNamespace(), state_store=state,
                          account=WeixinIlinkAccount(token="test-only", account_id="bot-b"))
