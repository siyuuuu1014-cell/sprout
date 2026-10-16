import argparse
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Sprout.tests import coding_acceptance as entry


@pytest.mark.parametrize("value", ["0", "21", "-1"])
def test_bad_request_limit(value):
    with pytest.raises(argparse.ArgumentTypeError):
        entry.request_limit(value)


def test_missing_explicit_budget_refuses_before_execution(monkeypatch):
    child = Mock()
    monkeypatch.setattr(entry.subprocess, "run", child)
    with pytest.raises(SystemExit):
        entry.main(["run", "--config", "x", "--output-dir", "y", "--coding-image", "z"])
    child.assert_not_called()


@pytest.mark.parametrize("sample", ["addition", "cart"])
def test_run_forwards_exact_allowance_without_enabling_commit(tmp_path, monkeypatch, sample):
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    child = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(entry.subprocess, "run", child)
    assert entry.run(config, tmp_path / "fresh", 4, "sha256:" + "a" * 64, sample) == 0
    command = child.call_args.args[0]
    environment = child.call_args.kwargs["env"]
    assert "test_deepseek_live_coding.py" in " ".join(command)
    assert environment["SPROUT_LIVE_REQUEST_LIMIT"] == "4"
    assert environment["SPROUT_LIVE_CONFIG"] == str(config.resolve())
    assert environment["SPROUT_ACCEPTANCE_SAMPLE"] == sample
    assert "commit" not in command


def test_existing_evidence_cannot_be_reused(tmp_path, monkeypatch):
    child = Mock()
    monkeypatch.setattr(entry.subprocess, "run", child)
    with pytest.raises(ValueError, match="Fresh"):
        entry.run(tmp_path / "config", tmp_path, 4, "sha256:" + "a" * 64)
    child.assert_not_called()


def test_commit_requires_explicit_review_confirmation(monkeypatch):
    child = Mock()
    monkeypatch.setattr(entry.asyncio, "run", child)
    with pytest.raises(SystemExit):
        entry.main(["commit", "--report", "x", "--config", "y", "--output", "z"])
    child.assert_not_called()


def test_status_rejects_report_pointing_outside_evidence(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"evidence_dir": str(tmp_path.parent), "task_id": "x"}),
                      encoding="utf-8")
    with pytest.raises(ValueError, match="own experiment"):
        entry.status(report)
