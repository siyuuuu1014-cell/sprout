from copy import deepcopy

import pytest

from Sprout.evolution.coding_profile import coding_profile, verify_coding_profile
from Sprout.evolution.replay_oracle import digest
from Sprout.evolution.replay_plan import build_plan, verify_plan


def plan(tmp_path):
    return build_plan(
        source_task_id="source", revision="a" * 40, mode="code",
        original=tmp_path / "original", repo=tmp_path / "repo",
        tests=("pytest tests",), regressions=("pytest tests",), oracle_paths=("tests",),
        frozen={"tests/test.py": "b" * 64}, image="sha256:" + "c" * 64,
        configuration={"schema_version": 1}, runtime={"schema_version": 2},
        creator={"source": "cli", "user_id": "fixture", "authenticated": True},
        coding=coding_profile("sha256:" + "d" * 64))


def test_v5_roundtrip_and_coding_image_bound(tmp_path):
    value = plan(tmp_path)
    assert value["payload"]["schema_version"] == 5
    assert verify_plan(value) == value["payload"]
    changed = deepcopy(value)
    changed["payload"]["coding_profile"]["image"] = "sha256:" + "e" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        verify_plan(changed)


@pytest.mark.parametrize("key,value", [("protocol", "host"), ("auxiliary_checks", "skip"),
                                       ("schema_version", True), ("image", "latest")])
def test_unsupported_boundary_rejected_even_if_rehashed(tmp_path, key, value):
    changed = plan(tmp_path)
    changed["payload"]["coding_profile"][key] = value
    changed["fingerprint"] = digest(changed["payload"])
    with pytest.raises(ValueError):
        verify_plan(changed)


def test_old_plan_is_not_automatically_backfilled(tmp_path):
    old = plan(tmp_path)
    old["payload"].pop("coding_profile")
    old["payload"]["schema_version"] = 4
    old["fingerprint"] = digest(old["payload"])
    assert "coding_profile" not in verify_plan(old)


def test_profile_verification_does_not_retain_mutable_input_alias():
    original = coding_profile("sha256:" + "a" * 64)
    checked = verify_coding_profile(original)
    original["protocol"] = "changed"
    assert checked["protocol"] == "node-json-v1"
