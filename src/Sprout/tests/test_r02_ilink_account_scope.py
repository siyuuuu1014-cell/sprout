"""Probe the real CLI state-path selection without starting a gateway/client."""

from types import SimpleNamespace

import pytest

from Sprout.cli.commands import gateway as cli_gateway
from Sprout.gateway.channels.weixin_ilink import WeixinIlinkAccount
from Sprout.gateway.channels.weixin_ilink_state import WeixinIlinkStateStore


async def test_distinct_unidentified_accounts_cannot_reuse_the_same_peer_state(
    tmp_path, monkeypatch
):
    stores = []

    class StopBeforeGatewayStart(Exception):
        pass

    def capture_store(*args, **kwargs):
        store = WeixinIlinkStateStore(*args, **kwargs)
        stores.append(store)
        return store

    def stop_gateway(*args, **kwargs):
        raise StopBeforeGatewayStart

    monkeypatch.setattr(cli_gateway, "WeixinIlinkStateStore", capture_store)
    monkeypatch.setattr(cli_gateway, "WeixinIlinkGateway", stop_gateway)
    settings = SimpleNamespace(weixin_ilink=SimpleNamespace(
        default_workspace_id="", dm_policy="closed", allowed_users=("test-peer",)
    ))
    # R02 fourth authorization requires rejection BEFORE any state constructor.
    # Preserve the collision probe, strengthening it to fail-closed admission.
    for account in (WeixinIlinkAccount(token="test-only-account-a"),
                    WeixinIlinkAccount(token="test-only-account-b")):
        with pytest.raises(ValueError, match="account ID"):
            await cli_gateway._run_weixin_ilink(
                None, settings, account, tmp_path / "context"
            )
    assert stores == []
    assert not (tmp_path / "state" / "account.json").exists()
