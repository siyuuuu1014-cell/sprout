"""Internal Apply target contracts. Intentional RED; final I/O is memory-only.

The fake index is an explicit Git semantic model, not a native Git experiment.
No private fixture imports from historical untracked investigations are used.
"""

import asyncio
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.execution import apply as apply_module
from Sprout.execution.apply import ApplyBroker
from Sprout.execution.models import ChangeProposal, ChangeProposalStatus, DiffResult
from Sprout.runtime.changes import ChangeProposalService
from Sprout.runtime.nodes import NodeExecutor
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.storage.local.memory import MemoryOperationalStore
from Sprout.task.models import DelegationScope, Task
from Sprout.workspace.models import Workspace

PATCH = ("diff --git a/task.txt b/task.txt\n--- a/task.txt\n+++ b/task.txt\n"
         "@@ -1 +1 @@\n-old\n+new\n")


@pytest.fixture
def probe(tmp_path, monkeypatch):
    state = SimpleNamespace(
        index=set(), committed=[], target_extra=False, contaminated=False,
        head="a" * 40, worktree_changed=False, failure=None, head_reads=0,
    )

    async def git(workspace, *args):
        if args[0] == "rev-parse":
            state.head_reads += 1
            if state.failure == ("observe", "nonzero") and state.head_reads > 1:
                return 1, "", "mock observation failure"
            return 0, state.head, ""
        phase = "preflight" if args[:2] == ("apply", "--check") else args[0]
        if phase == "apply":
            state.worktree_changed = True
        if phase == "add":
            state.index.update({"task.txt", "unrelated.txt"} if "-A" in args else args[2:])
            state.contaminated = state.target_extra
        if phase == "commit":
            state.committed.append(frozenset(state.index))
            state.head = "b" * 40
        # Fail after a possible effect; a nonzero exit is not no-effect evidence.
        if state.failure and state.failure[0] == phase:
            error = state.failure[1]
            if error == "timeout":
                raise TimeoutError("mock post-effect timeout")
            if error == "cancel":
                raise asyncio.CancelledError
            return 1, "", "mock post-effect failure"
        return 0, "", ""

    runner = AsyncMock(side_effect=git)
    undo = AsyncMock()
    writer = Mock(return_value=str(tmp_path / "never-created.patch"))
    unlink = Mock()
    monkeypatch.setattr(ApplyBroker, "_git", runner)
    monkeypatch.setattr(ApplyBroker, "_undo", undo)
    monkeypatch.setattr(ApplyBroker, "_write_patch", staticmethod(writer))
    monkeypatch.setattr(apply_module, "Path", lambda _: SimpleNamespace(unlink=unlink))
    capture = AsyncMock(return_value=(1, "", "mock legacy rollback denied"))
    spawn = AsyncMock(side_effect=AssertionError("Real process forbidden"))
    sync = Mock(side_effect=AssertionError("Real subprocess forbidden"))
    monkeypatch.setattr(apply_module, "run_capture", capture)
    monkeypatch.setattr("Sprout.execution.proc.run_capture", capture)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", spawn)
    monkeypatch.setattr(subprocess, "run", sync)
    monkeypatch.setattr(subprocess, "Popen", sync)
    yield SimpleNamespace(
        state=state, runner=runner, undo=undo, writer=writer, unlink=unlink,
        capture=capture, workspace=Workspace(id="ws", root=tmp_path),
    )
    spawn.assert_not_called()
    sync.assert_not_called()


def proposal(**changes):
    return replace(ChangeProposal(
        id="proposal", task_id="task", status=ChangeProposalStatus.APPROVED,
        files_changed=("task.txt",), diffs=(DiffResult("task.txt", PATCH),),
    ), **changes)


async def approved(item):
    manager = ApprovalManager(MemoryOperationalStore())
    record = await manager.request(
        "git.commit", {"proposal_id": item.id, "risk": item.risk},
        task_id=item.task_id, single_use=False,
    )
    await manager.decide(record.id, True, decided_by="test-human", channel="cli")
    return manager


async def attempt(broker, item, workspace, **kwargs):
    """Observe interruption; assertions below decide whether evidence is safe."""
    try:
        return await broker.apply(item, workspace, **kwargs)
    except (TimeoutError, asyncio.CancelledError) as exc:
        return exc


@pytest.mark.parametrize("boundary", ["empty_files", "staged_other", "target_extra", "mismatch"])
async def test_file_index_boundary_must_refuse_before_unreviewed_commit(probe, boundary):
    item = proposal(files_changed=() if boundary == "empty_files" else
                    ("other.txt",) if boundary == "mismatch" else ("task.txt",))
    if boundary == "staged_other":
        probe.state.index.add("operator.txt")
    probe.state.target_extra = boundary == "target_extra"
    result = await ApplyBroker(PolicyEngine(), approvals=await approved(item)).apply(
        item, probe.workspace)
    assert not probe.state.committed, "Unreviewed index/target/patch reached commit"
    assert not result.applied
    assert not any(call.args[1:3] == ("add", "-A") for call in probe.runner.await_args_list)


async def test_empty_patch_never_commits_existing_index(probe):
    item = proposal(diffs=())
    probe.state.index.add("operator.txt")
    await ApplyBroker(PolicyEngine(), approvals=await approved(item)).apply(item, probe.workspace)
    probe.runner.assert_not_called()
    probe.writer.assert_not_called()
    assert probe.state.index == {"operator.txt"} and not probe.state.committed


@pytest.mark.parametrize("kind,patch,files", [
    ("rename", "diff --git a/old b/new\nsimilarity index 100%\nrename from old\nrename to new\n",
     ("new",)),
    ("delete", "diff --git a/task.txt b/task.txt\ndeleted file mode 100644\n", ("task.txt",)),
    ("mode", "diff --git a/task.txt b/task.txt\nold mode 100644\nnew mode 100755\n", ("task.txt",)),
    ("symlink", "diff --git a/link b/link\nnew file mode 120000\n", ("link",)),
    ("binary", "diff --git a/image b/image\nGIT binary patch\nliteral 1\nA\n", ("image",)),
    ("escape", PATCH, ("../outside",)),
    ("pathspec", PATCH, (":(top,glob)**",)),
], ids=["rename", "delete", "mode", "symlink", "binary", "escape", "pathspec"])
async def test_unproven_patch_shapes_have_no_execution_contract(probe, kind, patch, files):
    item = proposal(diffs=(DiffResult(kind, patch),), files_changed=files)
    await ApplyBroker(PolicyEngine(), approvals=await approved(item)).apply(item, probe.workspace)
    assert probe.runner.await_count == 0, "Unproven patch/files must fail closed before Git"


async def test_direct_broker_cannot_treat_coarse_approval_as_verification(probe):
    item = proposal(status=ChangeProposalStatus.PENDING)
    result = await ApplyBroker(PolicyEngine(), approvals=await approved(item)).apply(
        item, probe.workspace)
    assert probe.runner.await_count == 0, "No trusted verification/approved snapshot was provided"
    assert not result.applied


@pytest.mark.parametrize("changed", ["patch", "files", "head", "workspace", "verification"])
async def test_old_approval_does_not_bind_a_changed_execution_plan(probe, changed):
    original = proposal()
    manager = await approved(original)
    item, workspace = original, probe.workspace
    if changed == "patch":
        item = replace(item, diffs=(DiffResult("task.txt", PATCH.replace("+new", "+other")),))
    elif changed == "files":
        item = replace(item, files_changed=("other.txt",))
    elif changed == "head":
        probe.state.head = "c" * 40
    elif changed == "workspace":
        workspace = replace(workspace, id="other-workspace", root=workspace.root / "other")
    else:
        item = replace(item, metadata={"verification": {"status": "passed", "origin": "claimed"}})
    result = await ApplyBroker(PolicyEngine(), approvals=manager).apply(item, workspace)
    assert not probe.state.committed, "Coarse old approval reopened a changed/unbound plan"
    assert not result.applied


@pytest.mark.parametrize("scope_kind", ["expired", "root_deny", "file_deny"])
async def test_real_scope_remains_authority(probe, scope_kind):
    item = proposal()
    scope = (DelegationScope(expires_at=datetime.now(UTC) - timedelta(seconds=1))
             if scope_kind == "expired" else DelegationScope(denied_paths=(
                 str(probe.workspace.root if scope_kind == "root_deny"
                     else probe.workspace.root / "task.txt"),)))
    result = await ApplyBroker(PolicyEngine(), approvals=await approved(item)).apply(
        item, probe.workspace, scope=scope)
    assert probe.runner.await_count == 0, "File Scope DENY cannot be replaced by root approval"
    assert not result.applied


async def test_real_policy_action_deny_precedes_existing_approval(probe):
    item = proposal()
    # The real PolicyEngine rejects git.commit under a file.read-only scope.
    scope = DelegationScope(allowed_actions=("file.read",))
    result = await ApplyBroker(PolicyEngine(), approvals=await approved(item)).apply(
        item, probe.workspace, scope=scope)
    assert not result.applied
    probe.runner.assert_not_called()
    probe.writer.assert_not_called()


@pytest.mark.parametrize("stage", ["apply", "add", "commit", "observe"])
@pytest.mark.parametrize("failure", ["timeout", "cancel", "nonzero"])
async def test_post_effect_interruption_requires_uncertain_evidence_without_undo(
    probe, stage, failure,
):
    item = proposal()
    probe.state.failure = (stage, failure)
    # Observation failure is injected on rev-parse, not an artificial Git stage.
    if stage == "observe" and failure != "nonzero":
        old = probe.runner.side_effect

        async def interrupted(workspace, *args):
            if args[0] == "rev-parse" and probe.state.committed:
                if failure == "timeout":
                    raise TimeoutError("mock observation timeout")
                raise asyncio.CancelledError
            return await old(workspace, *args)

        probe.runner.side_effect = interrupted
    result = await attempt(ApplyBroker(PolicyEngine(), approvals=await approved(item)),
                           item, probe.workspace)
    probe.undo.assert_not_called()
    assert not getattr(result, "applied", False), "Unknown effects cannot be reported delivered"
    assert ("UNCONFIRMED" in str(result).upper()
            or getattr(result, "effect", None) == "UNCONFIRMED"), (
        "Plain interruption/failed bool does not preserve execution uncertainty"
    )


@pytest.mark.parametrize("identity", ["short_oid", "moved_head"])
async def test_rollback_identity_requires_trusted_current_binding(probe, identity):
    item = proposal(metadata={"applied_commit": "abc" if identity == "short_oid" else "b" * 40,
                              "base_commit": "a" * 40})
    await ApplyBroker(PolicyEngine(), approvals=await approved(item)).rollback(
        item, probe.workspace)
    assert probe.runner.await_count == 0, "Rollback did not prove full OID/current HEAD identity"


async def test_conflicting_rollback_preserves_no_reset_contract(probe):
    item = proposal(metadata={"applied_commit": "b" * 40})
    probe.state.failure = ("revert", "nonzero")
    result = await ApplyBroker(PolicyEngine(), approvals=await approved(item)).rollback(
        item, probe.workspace)
    assert not result.applied and "manual reconciliation" in result.reason
    assert [call.args[1] for call in probe.runner.await_args_list] == ["revert"]
    probe.undo.assert_not_called()


@pytest.mark.parametrize("mode", ["concurrent", "restart"])
async def test_repeated_apply_requires_durable_one_shot_claim(probe, mode):
    item = proposal()
    manager = await approved(item)
    first = ApplyBroker(PolicyEngine(), approvals=manager)
    second = ApplyBroker(PolicyEngine(), approvals=manager)
    if mode == "concurrent":
        await asyncio.gather(
            first.apply(item, probe.workspace), second.apply(item, probe.workspace))
    else:
        await first.apply(item, probe.workspace)
        await second.apply(item, probe.workspace)
    assert len(probe.state.committed) <= 1, "No durable claim prevents duplicate Apply"


async def test_missing_approval_refuses_before_any_patch_io(probe):
    result = await ApplyBroker(PolicyEngine()).apply(proposal(), probe.workspace)
    assert not result.applied
    probe.runner.assert_not_called()
    probe.writer.assert_not_called()


def service_for(probe, item, manager):
    task = Task(id=item.task_id, workspace_id=probe.workspace.id)
    records = {item.id: item}

    async def save(value):
        records[value.id] = value

    metadata = SimpleNamespace(
        get_change_proposal=AsyncMock(side_effect=lambda key: records.get(key)),
        get_task=AsyncMock(return_value=task),
        get_workspace=AsyncMock(return_value=probe.workspace),
        list_change_proposals=AsyncMock(return_value=[item]),
        save_change_proposal=AsyncMock(side_effect=save),
    )
    broker = ApplyBroker(PolicyEngine(), approvals=manager)
    service = ChangeProposalService(metadata, manager._store, broker, approvals=manager)
    return service, metadata, task


@pytest.mark.parametrize("route", ["service", "node"])
async def test_existing_unverified_service_node_gate_is_preserved(probe, route):
    item = proposal()
    service, metadata, task = service_for(probe, item, await approved(item))
    if route == "service":
        result = await service.apply(item.id)
        assert not result.applied
    else:
        node = object.__new__(NodeExecutor)
        node._metadata, node._local_acceptance = metadata, True
        node._isolated_coding_required = False
        result = await node._apply(probe.workspace, task)
        assert not result["applied"]
    probe.runner.assert_not_called()
    probe.writer.assert_not_called()


async def test_force_audits_exception_but_is_not_execution_environment_authority(probe):
    item = proposal()
    service, metadata, _ = service_for(probe, item, await approved(item))
    result = await service.apply(item.id, force=True, override_reason="explicit test waiver")
    override = metadata.save_change_proposal.call_args_list[0].args[0].metadata[
        "verification_override"]
    assert override["verified"] is False and override["reason"] == "explicit test waiver"
    assert probe.runner.await_count == 0, "Force cannot supply missing trusted Apply executor"
    assert not result.applied


async def test_empty_file_set_refuses_before_all_io_and_has_no_broad_add(probe):
    import inspect

    item = proposal(files_changed=())
    result = await ApplyBroker(PolicyEngine(), approvals=await approved(item)).apply(
        item, probe.workspace)
    assert not result.applied and "files_changed" in result.reason
    probe.runner.assert_not_called()
    probe.writer.assert_not_called()
    probe.undo.assert_not_called()
    probe.capture.assert_not_called()
    probe.unlink.assert_not_called()
    assert not probe.state.committed
    source = inspect.getsource(ApplyBroker)
    assert '"-A"' not in source and '"--all"' not in source
