"""Legacy queue admission uses the same authoritative owner check as task.run."""
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from Sprout.gateway.rpc_gateway import RPCGateway
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.mark.parametrize("caller", ["missing", "unauthenticated", "other", "source", "owner"])
async def test_legacy_submit_requires_current_owner(rig, monkeypatch, caller):
    submit = AsyncMock()
    monkeypatch.setattr(rig.runtime, "submit_task", submit)
    principal = rig.principal
    if caller == "missing":
        principal = None
    elif caller == "unauthenticated":
        principal = replace(principal, authenticated=False)
    elif caller == "other":
        principal = replace(principal, user_id="not-owner")
    elif caller == "source":
        principal = replace(principal, source="forged-transport")
    response = await RPCGateway(rig.runtime).handle({
        "id": "submit", "method": "task.submit", "params": {
            "task_id": rig.task.id, "source": "cli", "transport": "cli",
            "metadata": {"authenticated": True},
        },
    }, principal=principal)
    if caller == "owner":
        assert response["result"] == {"task_id": rig.task.id, "queued": True}
        submit.assert_awaited_once_with(await rig.storage.metadata.get_task(rig.task.id))
        assert "status" not in response["result"]  # queued is not executed
    else:
        assert response["error"]["code"] == "FORBIDDEN"
        submit.assert_not_awaited()
