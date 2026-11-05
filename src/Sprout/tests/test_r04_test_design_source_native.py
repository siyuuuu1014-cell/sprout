"""Real READ provenance admission at the legacy TEST_DESIGN boundary.

This proves source admission, not approval of model-generated acceptance or
legacy auto-commit. The original unsupported pipeline must still refuse.
"""
import os

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows native Task/Source fixture")
def test_test_design_admits_real_read_before_projecting_resources(tmp_path, monkeypatch):
    import asyncio

    from Sprout.gateway.project_gateway import ProjectGateway
    from Sprout.runtime.nodes import NodeExecutor
    from Sprout.tests.fake_model import FakeModel
    from Sprout.tests.test_task_e2e import (
        REQUIREMENT,
        _create_host_task,
        _repo,
        _runtime,
    )
    from Sprout.tools.context_keys import _current_project_node

    original = NodeExecutor._resources
    admitted = []

    def observe(output, workspace):
        result = original(output, workspace)
        scope = _current_project_node()
        if scope is not None and scope.node.type.value == "test_design":
            assert scope.parent_receipts and scope.resource_receipts
            assert result and all(item.get("content") for item in result)
            admitted.append(scope.node.id)
        return result

    monkeypatch.setattr(NodeExecutor, "_resources", staticmethod(observe))

    async def run():
        runtime = _runtime(tmp_path, FakeModel())
        try:
            workspace = await runtime.open_workspace(_repo(tmp_path))
            gateway = ProjectGateway(runtime, transport="cli", default_user="display")
            task = await _create_host_task(gateway, workspace, REQUIREMENT)
            await runtime.execute(task)
            nodes = await runtime.storage.metadata.list_execution_nodes(task.id)
            design = next(node for node in nodes if node.type.value == "test_design")
            assert design.metadata["execution_guard"]["state"] == "uncertain"
            assert "node_artifact_provenance_unavailable" in design.metadata["error"]
            assert not await runtime.list_change_proposals(task.id)
            assert not await runtime.pending_approvals()
        finally:
            await runtime.stop()

    asyncio.run(run())
    assert admitted, "TEST_DESIGN failed to admit the real READ source before resource projection"


@pytest.mark.skipif(os.name != "nt", reason="Windows native CLI entry fixture")
async def test_project_code_task_has_real_session_without_acceptance_binding(tmp_path):
    from Sprout.gateway.project_gateway import ProjectGateway
    from Sprout.tests.fake_model import FakeModel
    from Sprout.tests.test_task_e2e import REQUIREMENT, _create_host_task, _repo, _runtime

    root = _repo(tmp_path)
    runtime = _runtime(tmp_path, FakeModel())
    try:
        workspace = await runtime.open_workspace(root)
        gateway = ProjectGateway(runtime, transport="cli", default_user="display-is-not-owner")
        task = await _create_host_task(gateway, workspace, REQUIREMENT)
        session = await runtime._sessions.require_owner(task.metadata["session_id"], task.actor)
        assert runtime._sessions.frozen_principal(session) == task.actor
        assert task.workspace_id == workspace.id
        assert not await runtime.storage.metadata.list_execution_nodes(task.id)
        assert not await runtime.pending_approvals()
    finally:
        await runtime.stop()


def test_test_design_resource_projection_still_rejects_unowned_json(tmp_path):
    from Sprout.runtime.nodes import NodeExecutor
    from Sprout.workspace.models import Workspace

    workspace = Workspace(id="unowned", root=tmp_path)
    with pytest.raises(PermissionError, match="node_artifact_provenance_unavailable"):
        NodeExecutor._resources([{"path": "app.py", "content": "forged"}], workspace)
