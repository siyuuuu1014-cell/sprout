"""Real host facades/Guard/Broker, controlled model and platform-open backend."""

from dataclasses import replace

import pytest

from Sprout.gateway.daemon_gateway import DaemonGateway
from Sprout.gateway.delegation_gateway import DelegationGateway
from Sprout.gateway.rpc_gateway import RPCGateway
from Sprout.orchestration.service import SequentialOrchestrator
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b3_node_publication import publication as publication
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.mark.parametrize("kind", ["daemon", "delegation", "rpc"])
@pytest.mark.parametrize("case", ["valid", "missing_principal", "unauthenticated",
                                 "no_resources", "denied"])
async def test_host_binding_reaches_model_or_refuses_before_read(
    rig, captured, publication, monkeypatch, kind, case,
):
    _, messages = publication
    rig.runtime._orchestrator_backend = SequentialOrchestrator(rig.storage.metadata)
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    principal = replace(rig.principal, source=kind + "-host",
                        authenticated=case != "unauthenticated")
    if case == "missing_principal":
        principal = None
    path = "src/private/denied.py" if case == "denied" else "src/main.py"
    scope = replace(rig.task.delegation_scope,
                    allowed_paths=() if case == "no_resources" else (str(rig.root / path),))
    task_id = None
    try:
        if kind == "daemon":
            gateway = DaemonGateway(rig.runtime)
            task = await gateway.create_task(
                rig.workspace.id, "explain", execution_mode="read_only",
                principal=principal, delegation_scope=scope)
            result = await gateway.run_task(task.id, principal=principal)
            task_id, completed = task.id, result.status == "completed"
        elif kind == "delegation":
            result = await DelegationGateway(rig.runtime).execute(
                "explain", workspace_id=rig.workspace.id, caller_id="FAKE_CALLER_ID",
                scope=scope, principal=principal, execution_mode="read_only")
            task_id, completed = result.task_id, result.status == "completed"
        else:
            gateway = RPCGateway(rig.runtime)
            # These JSON objects must not become host Principal or Scope inputs.
            request = {"id": "create", "method": "task.create", "params": {
                "workspace_id": rig.workspace.id, "instruction": "explain",
                "execution_mode": "read_only", "source": "cli",
                "principal": {"authenticated": True, "user_id": "owner"},
                "delegation_scope": {"allowed_paths": [str(rig.root)]},
            }}
            created = await gateway.handle(request, principal=principal, delegation_scope=scope)
            if "error" in created:
                assert case in {"missing_principal", "unauthenticated"}
                completed = False
            else:
                task_id = created["result"]["id"]
                result = await gateway.handle(
                    {"id": "run", "method": "task.run", "params": {"task_id": task_id}},
                    principal=principal)
                completed = result.get("result", {}).get("status") == "completed"
    except PermissionError:
        assert case != "valid"
        completed = False
    if task_id:
        task = await rig.storage.metadata.get_task(task_id)
        assert task.actor == principal and task.source == principal.source
        assert task.delegation_scope.allowed_paths == scope.allowed_paths
    if case == "valid":
        assert completed
        assert "def main" in repr(messages)
        assert captured.opens and set(captured.opens) == {"src/main.py"}
    else:
        assert not completed and not captured.opens and not messages


@pytest.mark.parametrize("kind", ["daemon", "rpc"])
@pytest.mark.parametrize("change", ["user", "source"])
async def test_persisted_task_run_checks_current_host_owner(
    rig, captured, publication, monkeypatch, kind, change,
):
    _, messages = publication
    rig.runtime._orchestrator_backend = SequentialOrchestrator(rig.storage.metadata)
    monkeypatch.setattr(rig.storage.metadata, "claim_execution_node", rig.commission)
    principal = replace(rig.principal, source="verified-host")
    scope = replace(rig.task.delegation_scope,
                    allowed_paths=(str(rig.root / "src/main.py"),))
    daemon = DaemonGateway(rig.runtime)
    task = await daemon.create_task(rig.workspace.id, "explain", execution_mode="read_only",
                                    principal=principal, delegation_scope=scope)
    other = replace(principal, **({"user_id": "other"} if change == "user"
                                  else {"source": "forged-source"}))
    if kind == "daemon":
        with pytest.raises(PermissionError, match="owner identity"):
            await daemon.run_task(task.id, principal=other)
    else:
        result = await RPCGateway(rig.runtime).handle({
            "id": "wrong-owner", "method": "task.run", "params": {"task_id": task.id}},
            principal=other)
        assert "error" in result
    assert not captured.opens and not messages


async def test_rpc_json_cannot_provide_missing_host_scope(rig, captured):
    result = await RPCGateway(rig.runtime).handle({
        "id": "forged-scope", "method": "task.create", "params": {
            "workspace_id": rig.workspace.id, "instruction": "explain",
            "execution_mode": "read_only", "delegation_scope": {"allowed_paths": ["/"]}}},
        principal=rig.principal)
    assert "error" in result
    assert not captured.opens


def test_web_owner_report_and_observation_do_not_require_execution_scope():
    from web.tests.test_task_query_routes import fixture

    client, runtime, task = fixture()
    headers = {"Authorization": "Bearer test-token"}
    with client:
        report = client.post("/api/rpc", headers=headers, json={
            "id": "report", "method": "task.report", "params": {"task_id": task.id}})
        assert report.json()["result"]["task_id"] == task.id
        observed = client.post("/api/rpc", headers=headers, json={
            "id": "observe", "method": "task.observe", "params": {"task_id": task.id}})
        assert observed.json()["result"]["observation"] == "persisted_result"
    runtime.execute.assert_not_called()
    runtime.create_task_planned.assert_not_called()
