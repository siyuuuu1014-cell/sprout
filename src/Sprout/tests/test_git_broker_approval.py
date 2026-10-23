"""Legacy approval compatibility without real Git fixtures.

B3 retires generic commit execution, not ApprovalManager's own grant semantics.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.git_broker import GitBroker
from Sprout.security.approval import ApprovalManager, ApprovalPolicy
from Sprout.security.engine import PolicyEngine
from Sprout.workspace.models import Workspace, WorkspaceKind

TOOL_NAME = "git_write"
COMMIT_ARGS = {"message": "x", "files": []}


@pytest.fixture(autouse=True)
def no_git_effects(monkeypatch):
    capture = AsyncMock(side_effect=AssertionError("Retired Git must never run"))
    spawn = AsyncMock(side_effect=AssertionError("Real child forbidden"))
    monkeypatch.setattr("Sprout.execution.git_broker.run_capture", capture)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    yield
    capture.assert_not_called()
    spawn.assert_not_called()


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(id="", root=tmp_path, kind=WorkspaceKind.LOCAL_DIRECTORY)


def _manager() -> ApprovalManager:
    from Sprout.storage.local.memory import MemoryOperationalStore

    return ApprovalManager(MemoryOperationalStore(), policy=ApprovalPolicy())


async def _granted(manager: ApprovalManager, tool: str, arguments: dict):
    record = await manager.request(tool, arguments, single_use=True)
    await manager.decide(record.id, True, decided_by="test", channel="cli")
    return await manager._store.get_approval(record.id)


async def test_a_grant_issued_under_the_tool_name_is_visible_to_the_broker(
    tmp_path: Path,
) -> None:
    """ApprovalManager lookup/consumption semantics remain unchanged."""
    manager = _manager()
    await _granted(manager, TOOL_NAME, COMMIT_ARGS)
    assert await manager.is_approved(TOOL_NAME, COMMIT_ARGS) is True


async def test_broker_commit_refuses_even_an_approval_granted_for_the_tool(
    tmp_path: Path,
) -> None:
    """Old positive host execution is intentionally retired; record untouched."""
    manager = _manager()
    approved = await _granted(manager, TOOL_NAME, COMMIT_ARGS)
    result = await GitBroker(PolicyEngine(), approvals=manager).commit(_workspace(tmp_path), "x")
    assert not result.allowed and "retired" in result.reason
    assert await manager._store.get_approval(approved.id) == approved


async def test_broker_refuses_a_commit_with_no_grant(tmp_path: Path) -> None:
    broker = GitBroker(PolicyEngine(), approvals=_manager())
    result = await broker.commit(_workspace(tmp_path), "x")
    assert not result.allowed and "retired" in result.reason


def test_the_tool_and_broker_names_are_declared_in_one_place() -> None:
    from Sprout.tools.system_tools import GitWriteTool

    broker_tools = {
        GitBroker.commit.__name__: GitWriteTool.spec.name,
        GitBroker.push.__name__: GitWriteTool.spec.name,
        GitBroker.pull.__name__: GitWriteTool.spec.name,
    }
    assert set(broker_tools.values()) == {TOOL_NAME}
