from copy import deepcopy

import pytest

from Sprout.evolution.replay_plan import build_plan, verify_plan


def plan(tmp_path):
    return build_plan(
        source_task_id="source", revision="a" * 40, mode="code",
        original=tmp_path / "original", repo=tmp_path / "repo",
        tests=("pytest tests",), regressions=("python -m pytest tests",),
        oracle_paths=("tests",), frozen={"tests/test_a.py": "b" * 64},
        image="sha256:" + "c" * 64,
    )


def test_plan_roundtrip(tmp_path):
    value = plan(tmp_path)
    assert verify_plan(value) == value["payload"]
    assert "settings" not in value["payload"]
    assert "instruction" not in value["payload"]


@pytest.mark.parametrize("field", ["revision", "verification_image", "test_commands"])
def test_changed_plan_rejected(tmp_path, field):
    value = deepcopy(plan(tmp_path))
    value["payload"][field] = "changed"
    with pytest.raises(ValueError):
        verify_plan(value)


def test_unsupported_plan_rejected(tmp_path):
    value = plan(tmp_path)
    value["payload"]["schema_version"] = 2
    with pytest.raises(ValueError):
        verify_plan(value)


def test_missing_fields_even_with_recomputed_hash_rejected(tmp_path):
    from Sprout.evolution.replay_oracle import digest

    value = plan(tmp_path)
    del value["payload"]["candidate"]
    value["fingerprint"] = digest(value["payload"])
    with pytest.raises(ValueError, match="fields"):
        verify_plan(value)
