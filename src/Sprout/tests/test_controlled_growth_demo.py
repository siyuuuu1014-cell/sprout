"""One controlled signal -> real measurement/publication -> actual coding consumption.

This composes proven test scenarios, not a production runner or autonomous-learning
claim. Published skill bytes from the first stage feed the second stage unchanged.
"""

import json
import os

import pytest

from Sprout.tests.test_isolated_coding_temporal_integration import (
    test_full_runtime_coding_graph_waits_for_approval as coding_scenario,
)
from Sprout.tests.test_validation_handoff_docker_integration import (
    test_real_pair_handoff_via_independent_cli as publication_scenario,
)


async def test_controlled_growth_demo(tmp_path, monkeypatch):
    if not all(os.getenv(key) for key in ("SPROUT_TEMPORAL_TEST_HOST",
            "SPROUT_CODING_TEST_IMAGE", "SPROUT_VERIFICATION_TEST_IMAGE")):
        pytest.skip("Explicit dedicated Temporal and both pinned Docker images required")
    publication, coding = {}, {}
    for stage in ("publication", "next-task"):
        (tmp_path / stage).mkdir()
    await publication_scenario(tmp_path / "publication", False, publication_sink=publication)
    await coding_scenario(tmp_path / "next-task", monkeypatch, False, False, True,
                          published_skill=publication, coding_sink=coding)
    assert coding["skill_read"] and coding["applied"] and coding["verified"]
    assert coding["skill_name"] == publication["name"]
    assert coding["skill_content_hash"] == publication["content_hash"]
    assert publication["publication_state"] == "completed"
    assert coding["task_status"] == "completed"
    result = {"schema_version": 1, "publication": publication, "next_coding_task": coding,
              "controlled_demo_passed": True, "autonomous_learning_proven": False,
              "coding_task_completed": coding["task_status"] == "completed",
              "improvement_proven": False, "scope": "fixed signal and scripted model; "
              "real paired Docker measurement, CLI publication and Temporal/Docker coding"}
    (tmp_path / "controlled-growth-demo.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
