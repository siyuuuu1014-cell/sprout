"""The offline evaluator is fail-closed and never reads a user's project."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_support.diagnostics import evaluation_failures
from Sprout.evaluation.fixtures import CASES
from Sprout.evaluation.runner import _repo, run_suite


@pytest.fixture
def eval_host(monkeypatch):
    # Controlled SID on Windows; never a claim of native identity verification.
    from Sprout.cli.commands.project import _eval_scope_for_workspace
    from Sprout.gateway.local_identity import current_cli_principal

    monkeypatch.setattr("Sprout.gateway.local_identity._windows_sid", lambda: "S-1-5-21-4700")
    return current_cli_principal(), _eval_scope_for_workspace


def test_generated_fixture_conftest_passes_ruff(tmp_path):
    case = next(case for case in CASES if case.id == "C01")
    repo = _repo(tmp_path, case)
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "conftest.py", "--no-cache"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_catalog_has_ten_stable_unique_cases():
    assert [case.id for case in CASES] == [
        "A01",
        "A02",
        "A03",
        "C01",
        "C02",
        "C03",
        "C04",
        "C05",
        "R01",
        "R02",
    ]


async def test_unknown_case_is_rejected_before_writes(tmp_path):
    with pytest.raises(ValueError, match="Unknown"):
        await run_suite(tmp_path, case_ids=("bad",))
    assert not list(tmp_path.iterdir())


async def test_remote_temporal_environment_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPORAL_HOST", "untrusted:7233")
    with pytest.raises(ValueError, match="TEMPORAL_HOST"):
        await run_suite(tmp_path)
    assert not list(tmp_path.iterdir())


async def test_fixed_suite_and_persisted_report(tmp_path, monkeypatch, eval_host):
    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    principal, provider = eval_host
    report = await run_suite(tmp_path, principal=principal, scope_provider=provider)
    evaluation_failures(report, tmp_path)
    assert report["scenario_passed"] == 10, json.dumps(
        [
            {"id": item["id"], "error": item.get("error"), "evidence": item["evidence_dir"]}
            for item in report["results"]
            if not item["scenario_passed"]
        ],
        indent=2,
    )
    assert report["completed_code_tasks"] == 4
    assert not report["model_quality_measured"]
    persisted = json.loads((Path(report["evidence_dir"]) / "report.json").read_text())
    assert persisted == report


def test_cli_non_green_eval_has_nonzero_exit(monkeypatch, eval_host):
    from typer.testing import CliRunner

    from Sprout.cli.commands import project
    from Sprout.evaluation import runner

    async def failed(output, *, case_ids, principal, scope_provider, preparation_provider):
        assert principal == eval_host[0]
        assert scope_provider is eval_host[1]
        assert preparation_provider is project._eval_inputs_for_workspace
        return {"total": 1, "scenario_passed": 0, "failure_stages": {"setup": 1}}

    monkeypatch.setattr(runner, "run_suite", failed)
    result = CliRunner().invoke(project.app, ["eval", "--case", "C02", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["scenario_passed"] == 0


async def test_broken_positive_fixture_is_reported_not_hidden(tmp_path, monkeypatch, eval_host):
    from Sprout.evaluation import runner

    monkeypatch.delenv("TEMPORAL_HOST", raising=False)
    original = runner._repo

    def broken(directory, case):
        repo = original(directory, case)
        (repo / "src/app.py").write_text("def clamp(value):\n    return max(0, value)\n")
        return repo

    monkeypatch.setattr(runner, "_repo", broken)
    principal, provider = eval_host
    report = await run_suite(tmp_path, case_ids=("C01",), principal=principal,
                             scope_provider=provider)
    assert report["scenario_passed"] == 0
    assert report["baseline_invalid"] == 1
