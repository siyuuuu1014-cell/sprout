from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.cli import commit_worker as module
from Sprout.cli import experiment_runtime as assembly
from Sprout.orchestration.isolated_worker import COMMIT_ROUTING_PROFILE


def setup(monkeypatch, tmp_path):
    runtime = SimpleNamespace(storage=SimpleNamespace(metadata=SimpleNamespace(
        get_task=AsyncMock(return_value=SimpleNamespace(metadata={"replay_plan": "plan"}))),
        close=AsyncMock()))
    factory = Mock(return_value=runtime)
    monkeypatch.setattr("Sprout.runtime.factory.create_runtime", factory)
    monkeypatch.setattr(module, "_regular", lambda path: path)
    monkeypatch.setattr(assembly, "_regular", lambda path: path)
    monkeypatch.setattr(module, "commit_status_report", lambda *a, **k: {
        "requests": [{"review": {"routing_profile": COMMIT_ROUTING_PROFILE}}]})
    monkeypatch.setattr(module, "_route", lambda *a: ("host", "namespace", "queue"))
    monkeypatch.setattr(assembly, "recovery_facts", lambda *a: {
        "metadata": {"replay_plan": "plan"}})
    monkeypatch.setattr(assembly, "verify_plan", lambda *a: {
        "configuration": {}, "coding_profile": {"image": "fixed"}})
    monkeypatch.setattr(assembly, "isolated_settings", lambda *a: SimpleNamespace(
        mcp=SimpleNamespace(clients=[])))
    monkeypatch.setattr(assembly, "configuration_blockers", lambda *a: [])
    return runtime, factory, tmp_path / "metadata.db"


async def test_foreground_uses_owner_without_generic_start(monkeypatch, tmp_path):
    runtime, factory, path = setup(monkeypatch, tmp_path)
    bound = object()
    constructor = Mock(return_value=bound)
    monkeypatch.setattr(module, "IsolatedCommitWorker", constructor)

    async def owned(worker):
        assert worker is bound
        runtime._isolated_lifecycle_owned = True
        await runtime.storage.close()

    monkeypatch.setattr(module, "run_owned_workers", owned)
    await module.serve_commit_worker(path, "task", "request", settings=None, principal=None)
    factory.assert_called_once()
    runtime.storage.close.assert_awaited_once()
    assert runtime._isolated_coding_image == "fixed"
    assert not hasattr(runtime, "_started")


async def test_assembly_failure_closes_untransferred_storage(monkeypatch, tmp_path):
    runtime, _, path = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "IsolatedCommitWorker", Mock(side_effect=ValueError("blocked")))
    with pytest.raises(ValueError, match="blocked"):
        await module.serve_commit_worker(path, "task", "request", settings=None, principal=None)
    runtime.storage.close.assert_awaited_once()


async def test_request_only_saves_review_and_closes_storage(monkeypatch, tmp_path):
    runtime, _, path = setup(monkeypatch, tmp_path)
    constructor = Mock()
    monkeypatch.setattr(module, "IsolatedCommitWorker", constructor)
    owner = AsyncMock()
    monkeypatch.setattr(module, "run_owned_workers", owner)
    request = AsyncMock(return_value={"id": "request", "fingerprint": "fp",
                                     "expires_at": "fixed expiry"})
    monkeypatch.setattr("Sprout.runtime.experiment_commits.ExperimentCommitService",
                        lambda runtime: SimpleNamespace(request=request))
    result = await module.create_cli_commit_request(path, "task", "proposal",
        settings=None, principal=None, message="Explicit", author_name="Author")
    assert result["request_id"] == "request"
    assert result["write_authorized"] is False
    request.assert_awaited_once_with("task", "proposal", message="Explicit",
        author_name="Author", author_email=None, evaluation_node_id=None)
    owner.assert_not_awaited()
    runtime.storage.close.assert_awaited_once()


@pytest.mark.parametrize("reason", ["legacy", "configuration", "missing_database"])
async def test_refusal_before_runtime_creation(monkeypatch, tmp_path, reason):
    _, factory, path = setup(monkeypatch, tmp_path)
    if reason == "legacy":
        monkeypatch.setattr(module, "commit_status_report", lambda *a, **k: {
            "requests": [{"review": {}}]})
    elif reason == "configuration":
        monkeypatch.setattr(assembly, "configuration_blockers", lambda *a: ["changed"])
    else:
        monkeypatch.setattr(module, "_regular", Mock(side_effect=ValueError("missing")))
    with pytest.raises(ValueError):
        await module.serve_commit_worker(path, "task", "request", settings=None, principal=None)
    factory.assert_not_called()


@pytest.mark.parametrize("failed", [False, True])
async def test_candidate_authority_is_loaded_and_closed_before_worker(
    monkeypatch, tmp_path, failed,
):
    runtime, factory, path = setup(monkeypatch, tmp_path)
    authority = SimpleNamespace(storage=SimpleNamespace(metadata=object(), close=AsyncMock()))
    factory.side_effect = [runtime, authority]
    monkeypatch.setattr(assembly, "verify_plan", lambda *a: {
        "configuration": {}, "coding_profile": {"image": "fixed"}, "candidate": {"id": "pinned"}})
    restore = AsyncMock(side_effect=ValueError("candidate changed") if failed else None)
    monkeypatch.setattr(assembly, "restore_candidate", restore)
    entered = False
    try:
        async with assembly.experiment_runtime(path, "task", settings=None):
            entered = True
            authority.storage.close.assert_awaited_once()
    except ValueError as exc:
        assert failed and str(exc) == "candidate changed"
    assert entered is not failed
    restore.assert_awaited_once_with(runtime, await runtime.storage.metadata.get_task("task"),
                                    authority.storage.metadata)
    authority.storage.close.assert_awaited_once()
    runtime.storage.close.assert_awaited_once()
    assert not hasattr(authority, "_started")


async def test_supplied_candidate_authority_is_not_owned_or_reopened(monkeypatch, tmp_path):
    runtime, factory, path = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(assembly, "verify_plan", lambda *a: {
        "configuration": {}, "coding_profile": {"image": "fixed"}, "candidate": {"id": "pinned"}})
    restore = AsyncMock()
    monkeypatch.setattr(assembly, "restore_candidate", restore)
    source = object()
    async with assembly.experiment_runtime(path, "task", settings=None, source_metadata=source):
        pass
    factory.assert_called_once()
    assert restore.await_args.args[-1] is source
    runtime.storage.close.assert_awaited_once()
