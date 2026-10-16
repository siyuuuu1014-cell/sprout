import json
from pathlib import Path

import pytest

from Sprout.evolution import comparison_refresh as module
from Sprout.tests.test_comparison_report import paired


def test_successor_pair_does_not_overwrite_original(monkeypatch, tmp_path):
    original = paired(tmp_path)
    before = original.read_bytes()
    pair = json.loads(before)
    for role in ("baseline", "candidate"):
        directory = Path(pair[role]["evidence_dir"])
        (directory / module.COMPLETED_MEASUREMENT).write_bytes(
            (directory / "independent-measurement.json").read_bytes())
        report_path = directory / module.COMPLETED_MEASUREMENT
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["measurement_status"] = "observed"
        report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(module, "recovery_facts", lambda *a: {"status": "completed"})
    result = module.refresh_comparison(original)
    assert Path(result["pair_manifest"]) != original and original.read_bytes() == before
    assert result["report"]["verdict"] == "observed_all_passed"
    assert result["validation_committed"] is False


def test_missing_completed_reports_do_not_create_successor(tmp_path):
    original = paired(tmp_path)
    before = set(tmp_path.glob("growth-comparison-*.json"))
    with pytest.raises(ValueError, match="regular evidence"):
        module.refresh_comparison(original)
    assert set(tmp_path.glob("growth-comparison-*.json")) == before
