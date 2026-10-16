from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Sprout.config.loader import default_settings
from Sprout.evolution.coding_profile import coding_profile
from Sprout.evolution.coding_readiness import inspect_coding_readiness
from Sprout.evolution.replay_config import configuration_contract
from Sprout.evolution.replay_plan import build_plan
from Sprout.evolution.replay_snapshot import runtime_snapshot
from Sprout.gateway.identity import Principal


def fixture(tmp_path):
    settings = default_settings()
    runtime = SimpleNamespace(tools=SimpleNamespace(list=lambda: {}),
                              skills=SimpleNamespace(list=lambda: {}), start=Mock(),
                              _isolated_coding_required=True)
    principal = Principal(user_id="fixture", source="cli", authenticated=True)
    image = "sha256:" + "a" * 64
    plan = build_plan(source_task_id="source", revision="b" * 40, mode="code",
        original=tmp_path / "original", repo=tmp_path / "repo", tests=("pytest",),
        regressions=("pytest",), oracle_paths=("tests",), frozen={"tests/a.py": "c" * 64},
        image=image, configuration=configuration_contract(settings),
        runtime=runtime_snapshot(runtime),
        creator={"user_id": "fixture", "source": "cli", "authenticated": True},
        coding=coding_profile(image))
    return runtime, settings, principal, plan


def test_missing_production_assembly_is_reported_without_starting_runtime(tmp_path):
    runtime, settings, principal, plan = fixture(tmp_path)
    result = inspect_coding_readiness(runtime, plan=plan, settings=settings, principal=principal)
    assert not result["configuration_and_assembly_checks_passed"]
    assert "isolated_coding_session_not_assembled" in result["blockers"]
    assert "isolated_coding_dispatch_not_assembled" in result["blockers"]
    assert not result["ready_to_execute"]
    runtime.start.assert_not_called()


def test_callable_test_stubs_and_image_binding_are_not_commissioning_proof(tmp_path):
    runtime, settings, principal, plan = fixture(tmp_path)
    runtime._isolated_coding_dispatch = Mock()
    runtime._isolated_coding_session_factory = Mock()
    runtime._isolated_coding_image = plan["payload"]["coding_profile"]["image"]
    result = inspect_coding_readiness(runtime, plan=plan, settings=settings, principal=principal)
    assert result["configuration_and_assembly_checks_passed"]
    assert not result["ready_to_execute"] and not result["write_authorized"]
    assert not result["validation_authorized"]
    runtime._isolated_coding_dispatch.assert_not_called()
    runtime._isolated_coding_session_factory.assert_not_called()


@pytest.mark.parametrize("change,blocker", [
    ("identity", "current_operator_not_experiment_creator"),
    ("configuration", "model_fingerprint_changed"),
    ("image", "isolated_coding_image_binding_not_assembled"),
    ("mode", "isolated_coding_mode_not_enabled"),
])
def test_identity_config_mode_or_image_drift_blocks_readiness(tmp_path, change, blocker):
    runtime, settings, principal, plan = fixture(tmp_path)
    runtime._isolated_coding_dispatch = Mock()
    runtime._isolated_coding_session_factory = Mock()
    runtime._isolated_coding_image = plan["payload"]["coding_profile"]["image"]
    if change == "identity":
        principal = Principal(user_id="other", source="cli", authenticated=True)
    elif change == "configuration":
        settings.model.model = "changed"
    elif change == "image":
        runtime._isolated_coding_image = "sha256:" + "d" * 64
    else:
        runtime._isolated_coding_required = False
    assert blocker in inspect_coding_readiness(
        runtime, plan=plan, settings=settings, principal=principal)["blockers"]


def test_corrupt_plan_is_not_upgraded(tmp_path):
    runtime, settings, principal, plan = fixture(tmp_path)
    altered = deepcopy(plan)
    altered["payload"]["coding_profile"]["image"] = "latest"
    result = inspect_coding_readiness(runtime, plan=altered, settings=settings, principal=principal)
    assert "coding_replay_plan_invalid" in result["blockers"]
    assert not result["ready_to_execute"]
