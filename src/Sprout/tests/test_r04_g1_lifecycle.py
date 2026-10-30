import asyncio
import json
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution import git_s1 as s
from Sprout.tools.result import ToolExecutionUnconfirmed


def command_records(issuer, frame, active):
    from Sprout.orchestration.models import NodeStatus
    from Sprout.task.models import DelegationScope, TaskStatus

    frame.task.status = TaskStatus.RUNNING
    frame.task.delegation_scope = DelegationScope()
    frame.node = SimpleNamespace(id="node")
    frame.guard_owner = "guard"
    frame.active = True
    frame.workspace = SimpleNamespace(id="workspace")
    node = SimpleNamespace(
        task_id=frame.task.id,
        status=NodeStatus.RUNNING,
        metadata={"execution_guard": {"state": "active", "owner": "guard"}},
    )
    store = SimpleNamespace(
        get_task=AsyncMock(return_value=frame.task),
        get_workspace=AsyncMock(return_value=frame.workspace),
        get_execution_node=AsyncMock(return_value=node),
    )
    issuer._metadata_or_raise = lambda: store
    issuer._verification_hosts[frame.task.id]["git"] = active["binding"]

    async def owner_check(*args):
        if asyncio.current_task() is not active["owner"]:
            raise PermissionError("real-owner-required")
        return frame.task, frame.workspace

    issuer._git_review_record = owner_check
    return store


@pytest.mark.parametrize("fault", ["none", "write-fail", "truncate"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_diagnostic_survives_failure_and_cancel(tmp_path, monkeypatch, cancel, fault):
    host = {}
    frame = SimpleNamespace(
        task=SimpleNamespace(id="task"), node=SimpleNamespace(id="node"), guard_owner="owner"
    )
    issuer = SimpleNamespace(_verification_hosts={"task": host}, _git_review_record=AsyncMock())
    from datetime import UTC, datetime

    node = SimpleNamespace(
        task_id="task",
        budget=SimpleNamespace(timeout_seconds=180),
        metadata={
            "execution_guard": {
                "owner": "owner",
                "state": "active",
                "started_at": datetime.now(UTC).isoformat(),
            }
        },
    )
    issuer._metadata_or_raise = lambda: SimpleNamespace(
        get_execution_node=AsyncMock(return_value=node)
    )
    issuer._git_review_state = lambda task_id: issuer._verification_hosts.get(task_id)
    frame.runtime = issuer
    binding = SimpleNamespace(parent=tmp_path, parent_identity="unit")

    @contextmanager
    def pin(root):
        yield SimpleNamespace(identity="unit")

    monkeypatch.setattr("Sprout.execution.host_acceptance.pin_execution_directory", pin)

    async def execute(*args):
        for _ in range(300 if fault == "truncate" else 1):
            s._review_trace(frame, "phase", "begin")
        if cancel:
            raise asyncio.CancelledError()
        raise TimeoutError()

    monkeypatch.setattr(s, "_run_review", execute)

    # Real exclusive filesystem recording is replaced only at the native write
    # boundary; no diagnostic can grant execution authority.
    def write(root, name, data):
        if fault == "write-fail":
            raise PermissionError("controlled diagnostic write denied")
        with (root / name).open("xb") as f:
            f.write(data)

    monkeypatch.setattr(s, "_write_view_file", write)
    with pytest.raises(asyncio.CancelledError if cancel else ToolExecutionUnconfirmed):
        await s.run_review(binding, issuer, frame, None)
    if fault == "write-fail":
        assert not list(tmp_path.glob("git-review-diagnostic-*.json"))
        assert "git_active" not in host
        return
    data = json.loads(next(tmp_path.glob("git-review-diagnostic-*.json")).read_text())
    if fault == "truncate":
        assert len(data["trace"]) == 256
    assert data["task"] == "task" and data["owner"] == "owner"
    assert data["trace"][0]["state"] == "begin"
    assert "git_active" not in host


async def test_spawn_cancel_without_handle_stays_unconfirmed(tmp_path, monkeypatch):
    binding = SimpleNamespace(
        paths=("src/app.py",), sandbox=SimpleNamespace(root=tmp_path), executable=Path("fixed-git")
    )
    frame = SimpleNamespace(task=SimpleNamespace(id="task"))
    active = {
        "binding": binding,
        "frame": frame,
        "owner": asyncio.current_task(),
        "view": tmp_path,
        "trace": [],
        "started": time.monotonic(),
    }
    issuer = SimpleNamespace(
        _verification_hosts={"task": {"git_active": active}}, _git_review_record=AsyncMock()
    )
    command_records(issuer, frame, active)
    issuer._git_review_state = lambda task_id: issuer._verification_hosts.get(task_id)
    frame.runtime = issuer
    broker = SimpleNamespace(_authorize_review=AsyncMock(), _check_review_policy=lambda *a: None)

    @contextmanager
    def pin(root):
        yield SimpleNamespace(check=lambda: None)

    monkeypatch.setattr("Sprout.execution.host_acceptance.pin_execution_directory", pin)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await s._review_command(
            binding,
            tmp_path,
            ("ls-files", "--stage", "-z", "--", "src/app.py"),
            AsyncMock(),
            issuer=issuer,
            frame=frame,
            broker=broker,
        )
    last = active["trace"][-1]
    assert last["state"] == "interrupted" and last["pid"] is None and not last["handle_acquired"]
    assert not any(x["state"] == "reaped" for x in active["trace"])


def test_budget_constants_are_separate():
    assert s.REVIEW_TOTAL_SECONDS == 90
    assert s.TIMEOUT_SECONDS == 30
    assert s.CLEANUP_SECONDS == 5


async def test_command_capture_timeout_and_reap_failure_remain_unknown(tmp_path, monkeypatch):
    from unittest.mock import Mock

    binding = SimpleNamespace(
        paths=("src/app.py",), sandbox=SimpleNamespace(root=tmp_path), executable=Path("fixed-git")
    )
    frame = SimpleNamespace(task=SimpleNamespace(id="task"))
    active = {
        "binding": binding,
        "frame": frame,
        "owner": asyncio.current_task(),
        "view": tmp_path,
        "trace": [],
        "started": time.monotonic(),
    }
    issuer = SimpleNamespace(
        _verification_hosts={"task": {"git_active": active}}, _git_review_record=AsyncMock()
    )
    command_records(issuer, frame, active)
    issuer._git_review_state = lambda task_id: issuer._verification_hosts.get(task_id)
    frame.runtime = issuer
    broker = SimpleNamespace(_authorize_review=AsyncMock(), _check_review_policy=lambda *a: None)

    @contextmanager
    def pin(root):
        yield SimpleNamespace(check=lambda: None)

    monkeypatch.setattr("Sprout.execution.host_acceptance.pin_execution_directory", pin)
    child = SimpleNamespace(
        pid=123, returncode=None, kill=Mock(), wait=AsyncMock(side_effect=TimeoutError())
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=child))
    monkeypatch.setattr(s, "capture_bounded", AsyncMock(side_effect=TimeoutError()))
    original = asyncio.wait_for
    budgets = []

    async def wait_for(op, timeout):
        budgets.append(timeout)
        return await original(op, timeout)

    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    with pytest.raises(ToolExecutionUnconfirmed):
        await s._review_command(
            binding,
            tmp_path,
            ("ls-files", "--stage", "-z", "--", "src/app.py"),
            AsyncMock(),
            issuer=issuer,
            frame=frame,
            broker=broker,
        )
    assert budgets == [30, 30, 5]
    child.kill.assert_called_once()
    assert active["trace"][-1]["state"] == "unconfirmed"
    assert not any(x["state"] == "reaped" for x in active["trace"])


async def test_insufficient_authoritative_node_budget_refuses_before_effect(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    frame = SimpleNamespace(
        task=SimpleNamespace(id="task"), node=SimpleNamespace(id="node"), guard_owner="owner"
    )
    node = SimpleNamespace(
        task_id="task",
        budget=SimpleNamespace(timeout_seconds=94),
        metadata={
            "execution_guard": {
                "owner": "owner",
                "state": "active",
                "started_at": datetime.now(UTC).isoformat(),
            }
        },
    )
    issuer = SimpleNamespace(
        _git_review_record=AsyncMock(),
        _metadata_or_raise=lambda: SimpleNamespace(get_execution_node=AsyncMock(return_value=node)),
    )
    effect = AsyncMock(side_effect=AssertionError("execution reached"))
    monkeypatch.setattr(s, "_run_review", effect)
    with pytest.raises(PermissionError, match="git_review_node_budget_insufficient"):
        await s.run_review(None, issuer, frame, None)
    effect.assert_not_called()


@pytest.mark.parametrize("mode", ["late", "repeat-cancel", "kill-fail", "wait-fail"])
async def test_owned_handle_survives_cancellation(tmp_path, monkeypatch, mode):
    from unittest.mock import Mock

    binding = SimpleNamespace(
        paths=("src/app.py",), sandbox=SimpleNamespace(root=tmp_path), executable=Path("fixed-git")
    )
    frame = SimpleNamespace(task=SimpleNamespace(id="task"))
    active = {
        "binding": binding,
        "frame": frame,
        "owner": None,
        "view": tmp_path,
        "trace": [],
        "started": time.monotonic(),
    }
    issuer = SimpleNamespace(
        _verification_hosts={"task": {"git_active": active}}, _git_review_record=AsyncMock()
    )
    command_records(issuer, frame, active)
    issuer._git_review_state = lambda task_id: issuer._verification_hosts.get(task_id)
    frame.runtime = issuer
    broker = SimpleNamespace(_authorize_review=AsyncMock(), _check_review_policy=lambda *a: None)
    spawn_entered, release_spawn, wait_entered, release_wait = [asyncio.Event() for _ in range(4)]
    child = SimpleNamespace(pid=456, returncode=None)

    def kill():
        if mode == "kill-fail":
            raise OSError("controlled kill failure")

    child.kill = Mock(side_effect=kill)

    async def wait():
        wait_entered.set()
        if mode == "wait-fail":
            raise TimeoutError("controlled wait failure")
        await release_wait.wait()
        child.returncode = -9
        return -9

    child.wait = wait

    async def spawn(*args, **kwargs):
        spawn_entered.set()
        await release_spawn.wait()
        return child

    async def capture(*args):
        raise TimeoutError("controlled capture timeout")

    @contextmanager
    def pin(root):
        yield SimpleNamespace(check=lambda: None)

    monkeypatch.setattr("Sprout.execution.host_acceptance.pin_execution_directory", pin)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(s, "capture_bounded", capture)

    async def command():
        active["owner"] = asyncio.current_task()
        return await s._review_command(
            binding,
            tmp_path,
            ("ls-files", "--stage", "-z", "--", "src/app.py"),
            AsyncMock(),
            issuer=issuer,
            frame=frame,
            broker=broker,
        )

    task = asyncio.create_task(command())
    await asyncio.wait_for(spawn_entered.wait(), 1)
    if mode == "late":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active["processes"] and child.returncode is None
        release_spawn.set()
    else:
        release_spawn.set()
    if mode not in {"kill-fail", "wait-fail"}:
        await asyncio.wait_for(wait_entered.wait(), 1)
        if mode == "repeat-cancel":
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        release_wait.set()
    if mode != "late":
        with pytest.raises(ToolExecutionUnconfirmed):
            await task
    # Await the exact retained reaper, never launch a replacement command.
    for custody in list(active.get("processes", {}).values()):
        if custody.get("cleanup") is not None:
            await asyncio.wait_for(asyncio.shield(custody["cleanup"]), 1)
    if mode in {"late", "repeat-cancel"}:
        assert not active["processes"]
        assert child.returncode == -9
        assert any(x["state"] == "reaped" for x in active["trace"])
    else:
        assert active["processes"]
        assert not any(x["state"] == "reaped" for x in active["trace"])
    child.kill.assert_called_once()


@pytest.mark.parametrize("change", ["owner-inactive", "scope-expired", "review-unbound", "guard"])
async def test_revocation_before_native_spawn_has_zero_effect(tmp_path, monkeypatch, change):
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    binding = SimpleNamespace(
        paths=("src/app.py",), sandbox=SimpleNamespace(root=tmp_path), executable=Path("fixed-git")
    )
    frame = SimpleNamespace(task=SimpleNamespace(id="task"))
    active = {
        "binding": binding,
        "frame": frame,
        "owner": asyncio.current_task(),
        "view": tmp_path,
        "trace": [],
        "started": time.monotonic(),
    }
    issuer = SimpleNamespace(_verification_hosts={"task": {"git_active": active}})
    store = command_records(issuer, frame, active)
    original = store.get_execution_node

    async def revoke(*args):
        node = await original(*args)
        if change == "owner-inactive":
            frame.active = False
        elif change == "scope-expired":
            frame.task.delegation_scope = replace(
                frame.task.delegation_scope, expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        elif change == "review-unbound":
            issuer._verification_hosts["task"].pop("git")
        else:
            node.metadata["execution_guard"]["state"] = "uncertain"
        return node

    store.get_execution_node = revoke
    issuer._git_review_state = lambda task_id: issuer._verification_hosts.get(task_id)
    frame.runtime = issuer
    broker = SimpleNamespace(_authorize_review=AsyncMock(), _check_review_policy=lambda *a: None)

    @contextmanager
    def pin(root):
        yield SimpleNamespace(check=lambda: None)

    monkeypatch.setattr("Sprout.execution.host_acceptance.pin_execution_directory", pin)
    effect = AsyncMock(side_effect=AssertionError("native create reached"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", effect)
    with pytest.raises(ToolExecutionUnconfirmed, match="git_spawn_authority_changed"):
        await s._review_command(
            binding,
            tmp_path,
            ("ls-files", "--stage", "-z", "--", "src/app.py"),
            AsyncMock(),
            issuer=issuer,
            frame=frame,
            broker=broker,
        )
    effect.assert_not_called()
