"""Old node results are data, not permission to publish them to a model."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.agent.base import ModelAgent
from Sprout.llm.messages import LLMResponse
from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
from Sprout.runtime.nodes import NodeExecutor
from Sprout.security.access import AccessDecision
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig
from Sprout.workspace.models import ReadPlan, ResourceRef
from Sprout.workspace.read_broker import ReadResult


@pytest.fixture
def publication(rig):
    seen = []
    class Model:
        async def chat(self, messages, **kwargs):
            seen.extend(messages)
            return LLMResponse(content="plain response")
    rig.runtime.register_agent("publication-test", ModelAgent(Model()), default=True)
    executor = rig.runtime._executor(rig.storage.metadata)
    return executor, seen


async def run(rig, publication):
    executor, _ = publication
    return await executor.execute_node(rig.node, rig.workspace,
                                       ReadPlan(task_id=rig.task.id), rig.task)


@pytest.mark.parametrize("branch", [NodeType.AGENT, NodeType.PLAN, NodeType.SUBTASK,
                                     NodeType.TEST_DESIGN])
@pytest.mark.parametrize("source", [NodeType.READ, NodeType.PLAN, NodeType.SUBTASK])
async def test_old_outputs_block_entire_model_publication(
    rig, publication, monkeypatch, branch, source,
):
    pending = ExecutionNode(id="publication-current", task_id=rig.task.id, type=branch,
        metadata=({"description": "derived old summary"} if branch is NodeType.SUBTASK else {}))
    await rig.storage.metadata.save_execution_node(pending)
    assert await rig.commission(pending, "publication-owner", "")
    rig.node = await rig.storage.metadata.get_execution_node(pending.id)
    if branch is NodeType.TEST_DESIGN:
        # Exercise its model-generated design branch, not the independent
        # frozen-spec verifier. No native sandbox or command is created.
        publication[0]._local_acceptance = False
        await rig.storage.metadata.save_execution_node(ExecutionNode(
            id="design-sandbox", task_id=rig.task.id, type=NodeType.SANDBOX,
            metadata={"output": {"id": "fixture", "kind": "git", "root": str(rig.root)}}))
        monkeypatch.setattr(publication[0]._file_broker, "write_text",
                            AsyncMock(side_effect=AssertionError("design write reached")))
    payload = ([{"path": str(rig.root / "src/private/old.py"), "decision": "allow",
                 "content": "OLD_WIDE_SNAPSHOT_SENTINEL"}]
               if source is NodeType.READ else {"plan": "OLD_WIDE_SNAPSHOT_SENTINEL",
                                                "findings": "OLD_WIDE_SNAPSHOT_SENTINEL"})
    await rig.storage.metadata.save_execution_node(ExecutionNode(
        id="old-output", task_id=rig.task.id, type=source, status=NodeStatus.COMPLETED,
        metadata={"output": payload}))
    rig.task = replace(rig.task, delegation_scope=replace(rig.task.delegation_scope,
        denied_paths=(str(rig.root / "src/private"),)))
    await rig.storage.metadata.save_task(rig.task)
    with pytest.raises(PermissionError):
        await run(rig, publication)
    assert publication[1] == []


@pytest.mark.parametrize("change", ["expired", "task", "principal", "session", "unknown"])
async def test_unverifiable_old_content_never_reaches_model(rig, publication, change):
    if change == "expired":
        rig.task = replace(rig.task, delegation_scope=replace(rig.task.delegation_scope,
            expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await rig.storage.metadata.save_task(rig.task)
    elif change in {"task", "principal", "session"}:
        task = replace(rig.task, workspace_id="other") if change == "task" else (
            replace(rig.task, actor=replace(rig.principal, user_id="other"))
            if change == "principal" else replace(rig.task, metadata={"session_id": "other"}))
        await rig.storage.metadata.save_task(task)
    else:
        await rig.storage.metadata.save_execution_node(ExecutionNode(
            id="unknown", task_id=rig.task.id, type=NodeType.READ,
            metadata={"output": [{"content": "UNKNOWN_ORIGIN"}]}))
    with pytest.raises(PermissionError):
        await run(rig, publication)
    assert publication[1] == []


async def test_plain_agent_input_still_runs(rig, publication):
    rig.task = replace(rig.task, instruction="plain original task input")
    await rig.storage.metadata.save_task(rig.task)
    result = await run(rig, publication)
    assert result["content"] == "plain response"
    assert "plain original task input" in repr(publication[1])


def test_core_legacy_read_helper_refuses_unbound_payload(rig):
    with pytest.raises(PermissionError):
        NodeExecutor._resources([{"path": str(rig.root / "src/private/old.py"),
            "decision": "allow", "content": "OLD_WIDE_SNAPSHOT_SENTINEL"}], rig.workspace)


@pytest.mark.parametrize("change", ["narrowed", "expired"])
async def test_actual_read_branch_then_revocation_before_model(
    rig, publication, monkeypatch, change,
):
    executor, seen = publication
    allowed = replace(rig.task.delegation_scope, denied_paths=())
    rig.task = replace(rig.task, delegation_scope=allowed)
    await rig.storage.metadata.save_task(rig.task)
    resource = ResourceRef(rig.workspace.id, str(rig.root / "src/private/old.py"))
    async def controlled_read(workspace, plan, *, scope):
        assert scope.permits("file.read", resource.path)
        return [ReadResult(resource, AccessDecision.ALLOW, "OLD_WIDE_SNAPSHOT_SENTINEL")]
    backend = AsyncMock(side_effect=controlled_read)
    monkeypatch.setattr(executor._read_broker, "read", backend)
    output = await executor._read(rig.workspace, ReadPlan(task_id=rig.task.id,
                                                       resources=(resource,)), rig.task)
    backend.assert_awaited_once()
    await rig.storage.metadata.save_execution_node(ExecutionNode(
        id="read-before-revoke", task_id=rig.task.id, type=NodeType.READ,
        status=NodeStatus.COMPLETED, metadata={"output": output}))
    scope = (replace(allowed, denied_paths=(str(rig.root / "src/private"),))
             if change == "narrowed" else replace(allowed,
                expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    rig.task = replace(rig.task, delegation_scope=scope)
    await rig.storage.metadata.save_task(rig.task)
    with pytest.raises(PermissionError):
        await run(rig, publication)
    assert seen == []


async def test_final_authority_recheck_blocks_revocation_during_preparation(
    rig, publication, monkeypatch,
):
    executor, seen = publication
    original = executor._check_model_publication
    checks = 0
    async def revoke_after_first(*args):
        nonlocal checks
        await original(*args)
        checks += 1
        if checks == 1:
            await rig.storage.metadata.save_task(replace(rig.task,
                delegation_scope=replace(rig.task.delegation_scope,
                    expires_at=datetime.now(UTC) - timedelta(seconds=1))))
    monkeypatch.setattr(executor, "_check_model_publication", revoke_after_first)
    with pytest.raises(PermissionError):
        await run(rig, publication)
    assert seen == []


@pytest.mark.parametrize("kind", [NodeType.READ, NodeType.PLAN, NodeType.SUBTASK,
                                  NodeType.EVALUATION, NodeType.TEST_DESIGN])
async def test_retry_does_not_approve_unverifiable_outputs(rig, publication, kind):
    await rig.storage.metadata.save_execution_node(ExecutionNode(
        id="old-retry-output", task_id=rig.task.id, type=kind,
        metadata={"output": {"content": "OLD_WIDE_SNAPSHOT_SENTINEL"}}))
    for _ in range(3):
        with pytest.raises(PermissionError):
            await run(rig, publication)
    assert publication[1] == []


async def test_cancelled_preparation_does_not_create_a_publication_grant(
    rig, publication, monkeypatch,
):
    executor, seen = publication
    with monkeypatch.context() as patch:
        patch.setattr(executor, "_check_model_publication",
                      AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await run(rig, publication)
    await rig.storage.metadata.save_execution_node(ExecutionNode(
        id="after-cancel", task_id=rig.task.id, type=NodeType.READ,
        metadata={"output": {"content": "OLD_WIDE_SNAPSHOT_SENTINEL"}}))
    with pytest.raises(PermissionError):
        await run(rig, publication)
    assert seen == []


@pytest.mark.parametrize("field", ["output", "description", "target_paths", "trusted",
                                   "provenance", "_sprout_context"])
async def test_node_metadata_cannot_certify_old_content(rig, publication, field):
    pending = ExecutionNode(id="metadata-publication", task_id=rig.task.id, type=NodeType.AGENT,
                            metadata={field: "OLD_WIDE_SNAPSHOT_SENTINEL"})
    await rig.storage.metadata.save_execution_node(pending)
    assert await rig.commission(pending, "metadata-owner", "")
    rig.node = await rig.storage.metadata.get_execution_node(pending.id)
    with pytest.raises(PermissionError):
        await run(rig, publication)
    assert publication[1] == []


@pytest.mark.parametrize("payload", [
    {"files": ("hidden.py",)}, {"knowledge": ("hidden",)}, {"memory": ("hidden",)},
    {"metadata": {"resources": ({"content": "hidden"},)}},
    {"metadata": {"plan": "hidden"}}, {"metadata": {"resume_tool_calls": [{"id": "old"}]}},
])
def test_direct_node_context_cannot_wrap_legacy_content(rig, payload):
    with pytest.raises(PermissionError):
        rig.runtime._contexts.build_node_context(user_id="owner", node_id=rig.node.id, **payload)


async def test_core_legacy_chain_denied_before_model(rig, publication):
    from Sprout.message.models import Message
    with pytest.raises(PermissionError):
        resources = NodeExecutor._resources([{"path": "src/private/old.py", "decision": "allow",
            "content": "OLD_WIDE_SNAPSHOT_SENTINEL"}], rig.workspace)
        prompt = NodeExecutor._agent_prompt(rig.task, rig.workspace, resources)
        context = rig.runtime._contexts.build_node_context(
            user_id="owner", node_id=rig.node.id, metadata={"resources": resources})
        message = Message(content=prompt, user_id="owner")
        await rig.runtime._router.route(message).run(message, context)
    assert publication[1] == []


@pytest.mark.parametrize("value", [False, {}, "", (), [{"decision": "deny", "path": "hidden"}]])
def test_malformed_or_denied_old_resources_are_not_visible(rig, value):
    with pytest.raises(PermissionError):
        NodeExecutor._resources(value, rig.workspace)


@pytest.mark.parametrize("metadata", [
    {"task_budget": {"summary": "old content"}}, {"task_budget": "old content"},
    {"task_budget": {"max_tokens": True}}, {"trusted": True},
])
def test_context_governance_fields_are_not_a_content_side_channel(rig, metadata):
    with pytest.raises(PermissionError):
        rig.runtime._contexts.build_node_context(user_id="owner", node_id=rig.node.id,
                                                 metadata=metadata)


@pytest.mark.parametrize("field", ["dependencies", "result_ref"])
async def test_persistent_references_without_output_cannot_authorize_publication(
    rig, publication, field,
):
    from Sprout.orchestration.models import DataRef
    pending = ExecutionNode(id="old-reference", task_id=rig.task.id, type=NodeType.AGENT,
        **({"dependencies": ("unproved-read",)} if field == "dependencies" else {
            "result_ref": DataRef("unproved-blob")}))
    await rig.storage.metadata.save_execution_node(pending)
    assert await rig.commission(pending, "reference-owner", "")
    rig.node = await rig.storage.metadata.get_execution_node(pending.id)
    with pytest.raises(PermissionError):
        await run(rig, publication)
    assert publication[1] == []


@pytest.mark.parametrize("kind", [NodeType.AGENT, NodeType.PLAN])
async def test_plain_model_nodes_with_no_dependencies_remain_available(rig, publication, kind):
    pending = ExecutionNode(id="plain-current", task_id=rig.task.id, type=kind)
    await rig.storage.metadata.save_execution_node(pending)
    assert await rig.commission(pending, "plain-owner", "")
    rig.node = await rig.storage.metadata.get_execution_node(pending.id)
    result = await run(rig, publication)
    assert "plain response" in repr(result)
    assert publication[1]


@pytest.mark.parametrize("origin", ["task", "principal", "session"])
async def test_copied_upstream_subject_labels_do_not_authorize_content(rig, publication, origin):
    # The consumer owns the current node; an upstream object's copied identity
    # labels cannot certify either its contents or current resource permission.
    payload = {"content": "OTHER_SUBJECT_SENTINEL", origin + "_id": "another-subject",
               "trusted": True, "scope": {"allowed_paths": ["/"]}}
    await rig.storage.metadata.save_execution_node(ExecutionNode(
        id="copied-upstream", task_id=rig.task.id, type=NodeType.PLAN,
        status=NodeStatus.COMPLETED, metadata={"output": payload}))
    with pytest.raises(PermissionError):
        await run(rig, publication)
    assert publication[1] == []


async def test_non_model_frozen_spec_verification_keeps_its_existing_contract(rig, publication):
    executor, seen = publication
    await rig.storage.metadata.save_execution_node(ExecutionNode(
        id="frozen-sandbox", task_id=rig.task.id, type=NodeType.SANDBOX,
        metadata={"output": {"id": "fixture", "kind": "git", "root": str(rig.root)}}))
    task = replace(rig.task, metadata={**rig.task.metadata, "acceptance_spec": {
        "cases": [{"id": "controlled", "target": "app:run", "expected_output": 1}]}})
    verify = AsyncMock(return_value={"status": "controlled-mock", "results": []})
    executor._isolated_coding_required = True
    executor._coding_session_factory = Mock(return_value=SimpleNamespace(verify=verify))
    result = await executor._test_design(rig.node, rig.workspace, task)
    verify.assert_awaited_once()
    assert result["status"] == "controlled-mock"
    assert seen == []  # Mock compatibility, NOT real sandbox/OS verification.
