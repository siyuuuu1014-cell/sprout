"""Deletion grants resume patches without mutating files before approval."""

from datetime import UTC, datetime, timedelta

import pytest

from Sprout.execution.file_broker import FileBroker
from Sprout.execution.models import SandboxRef
from Sprout.execution.sandbox_tool import SandboxApplyPatchTool
from Sprout.security.approval import ApprovalManager
from Sprout.security.engine import PolicyEngine
from Sprout.storage.local.memory import MemoryOperationalStore


@pytest.mark.asyncio
async def test_rename_waits_then_resumes_after_scoped_grant(tmp_path):
    old = tmp_path / "old.py"
    old.write_text("value = 1\n", encoding="utf-8")
    approvals = ApprovalManager(MemoryOperationalStore())
    broker = FileBroker(PolicyEngine(), approvals=approvals)
    sandbox = SandboxRef(id="sandbox", kind="git_worktree", root=tmp_path)
    tool = SandboxApplyPatchTool(sandbox, broker, task_id="task", requested_by="user")
    patch = {"patch": "diff --git a/old.py b/new.py\n"
             "similarity index 100%\nrename from old.py\nrename to new.py\n"}
    pending = await tool.invoke(patch)
    assert pending.approval_id
    assert old.exists() and not (tmp_path / "new.py").exists()
    assert (await tool.invoke(patch)).approval_id == pending.approval_id
    await approvals.decide(pending.approval_id, approved=True, decided_by="operator")
    other = await broker.authorize_delete(sandbox, "old.py", task_id="other",
                                         requested_by="user")
    assert not other.wrote
    result = await tool.invoke(patch)
    assert result.ok
    assert not old.exists()
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.asyncio
async def test_expired_deletion_grant_cannot_change_files(tmp_path):
    target = tmp_path / "old.py"
    target.write_text("value = 1\n", encoding="utf-8")
    store = MemoryOperationalStore()
    approvals = ApprovalManager(store)
    broker = FileBroker(PolicyEngine(), approvals=approvals)
    sandbox = SandboxRef(id="sandbox", kind="git_worktree", root=tmp_path)
    pending = await broker.delete(sandbox, "old.py", task_id="task")
    record = await approvals.decide(pending.approval_id, approved=True, decided_by="operator")
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await store.save_approval(record)
    result = await broker.delete(sandbox, "old.py", task_id="task")
    assert not result.wrote and target.exists()
    assert result.approval_id and result.approval_id != pending.approval_id
