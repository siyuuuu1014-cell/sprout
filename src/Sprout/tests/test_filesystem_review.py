import json

import pytest

from Sprout.execution.filesystem_review import capture_changes, checked_changes


def test_review_survives_sandbox_removal_and_rejects_tampering(tmp_path):
    from Sprout.sandbox.filesystem import FilesystemSandbox, capture_snapshot

    original = tmp_path / "source"
    original.mkdir()
    (original / "app.py").write_text("old", encoding="utf-8")
    with FilesystemSandbox(original) as sandbox:
        ref, _ = sandbox.create(("app.py",), lambda _: None)
        before = {"app.py": capture_snapshot(original, original / "app.py", lambda: None),
                  "test_app.py": capture_snapshot(original, original / "test_app.py", lambda: None)}
        (ref.root / "app.py").write_text("new", encoding="utf-8")
        (ref.root / "test_app.py").write_text("test", encoding="utf-8")
        review = capture_changes(sandbox, before, "task", "workspace", lambda _: None)
    assert not ref.root.exists()
    review = json.loads(json.dumps(review))
    with pytest.raises(ValueError):
        checked_changes(review, "different-task", "workspace")
    changes = checked_changes(review, "task", "workspace")["changes"]
    assert [item["kind"] for item in changes] == ["modified", "added"]
    assert changes[0]["after"]["content"] == "new"
    assert (original / "app.py").read_text(encoding="utf-8") == "old"
    changes[0]["after"]["content"] = "tampered"
    with pytest.raises(ValueError):
        checked_changes(review, "task", "workspace")

def test_review_conflict_and_no_change(tmp_path):
    from Sprout.sandbox.filesystem import FilesystemSandbox

    original = tmp_path / "source"
    original.mkdir()
    source = original / "app.py"
    source.write_text("old", encoding="utf-8")
    with FilesystemSandbox(original) as sandbox:
        sandbox.create(("app.py",), lambda _: None)
        review = capture_changes(sandbox, sandbox.sources, "task", "workspace", lambda _: None)
        assert checked_changes(review, "task", "workspace")["changes"] == []
        source.write_text("concurrent update", encoding="utf-8")
        review = capture_changes(sandbox, sandbox.sources, "task", "workspace", lambda _: None)
        assert checked_changes(review, "task", "workspace")["conflicts"] == ["app.py"]


@pytest.mark.parametrize("path", ["../app.py", ".env", "secrets.db"])
def test_review_refuses_unconfined_or_private_candidate(tmp_path, path):
    from Sprout.sandbox.filesystem import FilesystemSandbox

    original = tmp_path / "source"
    original.mkdir()
    (original / "app.py").write_text("old", encoding="utf-8")
    with FilesystemSandbox(original) as sandbox:
        sandbox.create(("app.py",), lambda _: None)
        with pytest.raises((PermissionError, ValueError)):
            capture_changes(sandbox, {path: sandbox.sources["app.py"]},
                            "task", "workspace", lambda _: None)


def test_review_refuses_symlink_and_deletion(tmp_path):
    from Sprout.sandbox.filesystem import FilesystemSandbox

    original = tmp_path / "source"
    original.mkdir()
    source = original / "app.py"
    source.write_text("old", encoding="utf-8")
    with FilesystemSandbox(original) as sandbox:
        ref, _ = sandbox.create(("app.py",), lambda _: None)
        target = ref.root / "app.py"
        target.unlink()
        with pytest.raises(ValueError, match="deletion_unsupported"):
            capture_changes(sandbox, sandbox.sources, "task", "workspace", lambda _: None)
        try:
            target.symlink_to(source)
        except OSError:
            pytest.skip("Windows symlink creation unavailable")
        with pytest.raises((OSError, ValueError)):
            capture_changes(sandbox, sandbox.sources, "task", "workspace", lambda _: None)

def test_new_output_conflicts_if_source_is_created_during_experiment(tmp_path):
    from Sprout.sandbox.filesystem import FilesystemSandbox, capture_snapshot

    original = tmp_path / "source"
    original.mkdir()
    (original / "app.py").write_text("old", encoding="utf-8")
    output = original / "test_app.py"
    with FilesystemSandbox(original) as sandbox:
        ref, _ = sandbox.create(("app.py",), lambda _: None)
        before = {"test_app.py": capture_snapshot(original, output, lambda: None, limit=0)}
        output.write_text("external change", encoding="utf-8")
        (ref.root / "test_app.py").write_text("experiment output", encoding="utf-8")
        review = capture_changes(sandbox, before, "task", "workspace", lambda _: None)
        payload = checked_changes(review, "task", "workspace")
        assert payload["conflicts"] == ["test_app.py"]
        assert payload["changes"][0]["kind"] == "added"
        assert payload["apply_available"] is False
        assert output.read_text(encoding="utf-8") == "external change"

def test_filesystem_apply_requires_independent_evidence_even_after_command_passes(tmp_path):
    from Sprout.execution.filesystem_review import apply_gate
    from Sprout.sandbox.filesystem import FilesystemSandbox

    source = tmp_path / "app.py"
    source.write_text("old", encoding="utf-8")
    with FilesystemSandbox(tmp_path) as sandbox:
        ref, _ = sandbox.create(("app.py",), lambda _: None)
        (ref.root / "app.py").write_text("new", encoding="utf-8")
        review = capture_changes(
            sandbox, sandbox.sources, "task", "workspace", lambda _: None,
            command={"status": "completed", "exit_code": 0},
        )
    assert apply_gate(review, "task", "workspace")["error"] == "filesystem_verification_required"
    assert source.read_text(encoding="utf-8") == "old"


def test_filesystem_apply_no_changes_has_no_write(tmp_path):
    from Sprout.execution.filesystem_review import apply_gate
    from Sprout.sandbox.filesystem import FilesystemSandbox

    (tmp_path / "app.py").write_text("original", encoding="utf-8")
    with FilesystemSandbox(tmp_path) as sandbox:
        sandbox.create(("app.py",), lambda _: None)
        review = capture_changes(sandbox, sandbox.sources, "task", "workspace", lambda _: None)
    assert apply_gate(review, "task", "workspace") == {"applied": False, "status": "no_changes"}

async def test_filesystem_apply_node_requires_completed_bound_review():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
    from Sprout.runtime.nodes import NodeExecutor

    task = SimpleNamespace(id="task", metadata={"filesystem_experiment": True})
    workspace = SimpleNamespace(id="workspace")
    review = ExecutionNode(id="task:approval", task_id="other", type=NodeType.APPROVAL,
                           status=NodeStatus.COMPLETED)
    executor = SimpleNamespace(
        _metadata=SimpleNamespace(get_execution_node=AsyncMock(return_value=review)),
        _apply=AsyncMock(),
    )
    node = ExecutionNode(id="task:apply", task_id=task.id, type=NodeType.APPLY,
                         dependencies=(review.id,))
    result = await NodeExecutor._execute_node(executor, node, workspace, None, task)
    assert result["error"] == "filesystem_review_evidence_mismatch"
    executor._apply.assert_not_awaited()

def test_verification_manifest_binds_every_candidate_file(tmp_path):
    from Sprout.execution.filesystem_review import verification_manifest
    from Sprout.sandbox.filesystem import FilesystemSandbox, capture_snapshot

    (tmp_path / "app.py").write_text("original", encoding="utf-8")
    with FilesystemSandbox(tmp_path) as sandbox:
        ref, _ = sandbox.create(("app.py",), lambda _: None)
        before = {"test_app.py": capture_snapshot(
            tmp_path, tmp_path / "test_app.py", lambda: None)}
        (ref.root / "test_app.py").write_text("test", encoding="utf-8")
        review = capture_changes(sandbox, before, "task", "workspace", lambda _: None)
    payload = checked_changes(review, "task", "workspace")
    expected = verification_manifest(payload)
    assert expected == {
        "app.py": payload["sources"]["app.py"]["sha256"],
        "test_app.py": payload["changes"][0]["after"]["sha256"],
    }

def test_verification_receipt_cannot_be_reused_for_another_changeset(tmp_path):
    from Sprout.execution.filesystem_review import verification_manifest, verification_status
    from Sprout.orchestration.models import ExecutionNode, NodeStatus, NodeType
    from Sprout.sandbox.filesystem import FilesystemSandbox

    (tmp_path / "app.py").write_text("before", encoding="utf-8")
    with FilesystemSandbox(tmp_path) as sandbox:
        ref, _ = sandbox.create(("app.py",), lambda _: None)
        (ref.root / "app.py").write_text("first change", encoding="utf-8")
        first = capture_changes(sandbox, sandbox.sources, "task", "workspace", lambda _: None)
        (ref.root / "app.py").write_text("second change", encoding="utf-8")
        second = capture_changes(sandbox, sandbox.sources, "task", "workspace", lambda _: None)
    node = ExecutionNode(
        id="task:evaluation", task_id="task", type=NodeType.EVALUATION,
        status=NodeStatus.COMPLETED,
        metadata={
            "execution_guard": {"state": "completed", "owner": "host"},
            "output": {
                "task_id": "task", "workspace_id": "workspace", "node_id": "task:evaluation",
                "owner": "host", "changeset_sha256": first["fingerprint"],
                "input_manifest": verification_manifest(first["payload"]),
                "verified": True, "status": "passed",
                "test_results": [{"passed": True, "executed": True}],
            },
        },
    )
    assert verification_status(node, first, "task", "workspace") == "passed"
    assert verification_status(node, second, "task", "workspace") == "not_run"
    assert verification_status(node, first, "task", "other") == "not_run"

async def test_independent_verification_rejects_replaced_candidate_after_intent(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from Sprout.execution.isolated_acceptance import verify_isolated_cases
    from Sprout.execution.verification_run import input_manifest

    exported = tmp_path / "input"
    exported.mkdir()
    source = exported / "app.py"
    source.write_text("reviewed", encoding="utf-8")
    expected = input_manifest(exported)

    async def save_intent(_):
        source.write_text("replacement", encoding="utf-8")

    store = SimpleNamespace(ensure_coding_intent=AsyncMock(side_effect=save_intent))
    context = SimpleNamespace(task_id="task", exported=exported, image="fixture", evidence=tmp_path)
    node = SimpleNamespace(id="evaluation", task_id="task",
                           metadata={"execution_guard": {"owner": "host"}})
    spec = {"cases": [{"id": "add", "target": "app:add", "expected_output": 5}]}
    with pytest.raises(ValueError, match="verification_candidate_changed"):
        await verify_isolated_cases(
            store, None, context=context, node=node, acceptance_spec=spec,
            regression_spec=spec, expected_manifest=expected)
    store.ensure_coding_intent.assert_awaited_once()
