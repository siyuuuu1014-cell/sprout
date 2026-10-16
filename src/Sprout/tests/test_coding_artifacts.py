import pytest

from Sprout.execution.coding_artifacts import artifact_plan
from Sprout.execution.verification_run import input_manifest


def test_changes_planned_without_writing_or_approving(tmp_path):
    (tmp_path / "app.py").write_text("old", encoding="utf-8")
    (tmp_path / "remove.py").write_text("old", encoding="utf-8")
    baseline = input_manifest(tmp_path)
    (tmp_path / "app.py").write_text("new", encoding="utf-8")
    (tmp_path / "add.py").write_text("new", encoding="utf-8")
    (tmp_path / "remove.py").unlink()
    before = input_manifest(tmp_path)
    plan = artifact_plan(tmp_path, baseline, oracle_paths=("tests",))
    assert [item["operation"] for item in plan["changes"]] == ["add", "modify", "delete"]
    assert plan["changes"][-1]["requires_delete_approval"] is True
    assert plan["applied"] is False and plan["authorization_granted"] is False
    assert input_manifest(tmp_path) == before


@pytest.mark.parametrize("path", ["tests/test_oracle.py", ".git/config", ".env"])
def test_protected_or_private_artifacts_rejected(tmp_path, path):
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("new", encoding="utf-8")
    with pytest.raises(ValueError):
        artifact_plan(tmp_path, {}, oracle_paths=("tests",))


def test_bad_baseline_cannot_request_external_delete(tmp_path):
    with pytest.raises(ValueError):
        artifact_plan(tmp_path, {"../host.db": "a" * 64}, oracle_paths=("tests",))


@pytest.mark.parametrize("cache", [".pytest_cache", ".ruff_cache", "__pycache__"])
def test_generated_cache_is_retained_but_not_imported(tmp_path, cache):
    (tmp_path / "app.py").write_text("old", encoding="utf-8")
    baseline = input_manifest(tmp_path)
    (tmp_path / "app.py").write_text("new", encoding="utf-8")
    generated = tmp_path / cache / "generated.bin"
    generated.parent.mkdir()
    generated.write_bytes(b"\x00cache")
    before = input_manifest(tmp_path)
    plan = artifact_plan(tmp_path, baseline, oracle_paths=("tests",))
    assert [change["path"] for change in plan["changes"]] == ["app.py"]
    assert input_manifest(tmp_path) == before


@pytest.mark.parametrize("path", ["tests/.pytest_cache/result", ".git/.pytest_cache/result"])
def test_generated_cache_does_not_hide_protected_or_private_root(tmp_path, path):
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text("cache", encoding="utf-8")
    with pytest.raises(ValueError):
        artifact_plan(tmp_path, {}, oracle_paths=("tests",))


def test_cache_in_trusted_baseline_is_not_silently_dropped(tmp_path):
    with pytest.raises(ValueError, match="trusted source baseline"):
        artifact_plan(tmp_path, {".pytest_cache/result": "a" * 64}, oracle_paths=("tests",))
