"""R01 test infrastructure contracts; no real infrastructure is invoked."""

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci_baseline
from scripts.ci_support.pytest_gates import gates_for

pytest_plugins = ["pytester"]


@pytest.mark.parametrize("directory", ["scripts", "src/Sprout/tests/fixtures", ".github/workflows"])
def test_snapshot_excludes_untracked_sensitive_files_without_deleting_them(
    tmp_path, monkeypatch, directory
):
    source = tmp_path / "source"
    folder = source / directory
    folder.mkdir(parents=True)
    tracked = folder / "tracked.txt"
    tracked.write_text("versioned fixture", encoding="utf-8")
    sensitive = folder / "operator-local.json"
    contents = b'{"key":"TEST-ONLY-NOT-A-REAL-SECRET"}'
    sensitive.write_bytes(contents)
    subprocess.run(["git", "init", "-q", str(source)], check=True, capture_output=True)
    relative = tracked.relative_to(source).as_posix()
    subprocess.run(["git", "add", "--", relative], cwd=source, check=True, capture_output=True)
    destination = tmp_path / "evidence" / "checkout"
    destination.mkdir(parents=True)
    monkeypatch.setattr(ci_baseline, "ROOT", source)

    ci_baseline.snapshot(destination)

    assert (destination / relative).read_text(encoding="utf-8") == "versioned fixture"
    assert not (destination / sensitive.relative_to(source)).exists()
    manifest = json.loads((destination.parent / "source-manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {relative}
    assert sensitive.read_bytes() == contents


def _run_gate(pytester, monkeypatch, *arguments):
    # Full collection already imports the real integration module. Use a fresh
    # interpreter for same-named synthetic tests, with a local pytest root/cache.
    root = str(Path(__file__).resolve().parents[3])
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            filter(
                None,
                (
                    root,
                    os.environ.get("PYTHONPATH", ""),
                ),
            )
        ),
    )
    pytester.makeini("[pytest]\nasyncio_default_fixture_loop_scope = function\n")
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *arguments)


@pytest.mark.parametrize(
    "filename,name,params,expected",
    [
        ("test_skills_disclosure.py", "test_eager_mode_keeps_full_body", {}, []),
        (
            "test_live_model_budget.py",
            "test_concurrent_calls_never_exceed_authorized_limit",
            {},
            [],
        ),
        ("test_isolated_worker.py", "test_bad_assembly_cannot_install_session", {}, []),
        (
            "test_isolated_worker.py",
            "test_real_sdk_instance_activity_registration",
            {},
            ["temporal"],
        ),
        (
            "test_replay_functions.py",
            "test_real_function_container_and_durable_cache",
            {},
            ["docker"],
        ),
        (
            "test_deepseek_live_coding.py",
            "test_real_deepseek_minimal_coding",
            {},
            ["docker", "model", "temporal"],
        ),
        (
            "test_gateway_process_integration.py",
            "test_gateway_submit_worker_observe_sync_processes",
            {"worker_mode": "cli"},
            ["temporal"],
        ),
        (
            "test_gateway_process_integration.py",
            "test_gateway_submit_worker_observe_sync_processes",
            {"worker_mode": "full-cli-code-live"},
            ["model", "temporal"],
        ),
        (
            "test_temporal_integration.py",
            "test_real_runtime_submission_and_history_replay",
            {"mode": "server-outage"},
            ["docker_faults", "temporal"],
        ),
        ("test_sixlane.py", "test_mirrored_memory_store_live", {}, ["databases"]),
    ],
)
def test_external_gates_do_not_hide_offline_contracts(filename, name, params, expected):
    item = SimpleNamespace(
        path=Path(filename), originalname=name, name=name, callspec=SimpleNamespace(params=params)
    )
    assert gates_for(item) == expected


def test_explicit_gate_missing_prerequisites_blocks_instead_of_skipping(pytester, monkeypatch):
    monkeypatch.delenv("SPROUT_CODING_TEST_IMAGE", raising=False)
    monkeypatch.delenv("SPROUT_VERIFICATION_TEST_IMAGE", raising=False)
    pytester.makepyfile(test_coding_docker_integration="def test_effect(): assert False")
    result = _run_gate(
        pytester, monkeypatch, "-p", "scripts.ci_support.pytest_gates", "--integration-gates=docker"
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*integration BLOCKED*"])


def test_l1_records_external_not_run_without_hiding_offline_failures(pytester, monkeypatch):
    pytester.makepyfile(
        test_coding_docker_integration="def test_effect(): assert False",
        test_offline="def test_contract(): assert False",
    )
    result = _run_gate(
        pytester,
        monkeypatch,
        "-p",
        "scripts.ci_support.pytest_gates",
        "--baseline-l1",
        "--gate-report=inventory.json",
    )
    result.assert_outcomes(failed=1, deselected=1)
    assert '"status": "not_run"' in (pytester.path / "inventory.json").read_text()


def test_explicit_gate_skips_are_not_a_green_integration(pytester, monkeypatch):
    monkeypatch.setenv("SPROUT_CODING_TEST_IMAGE", "fixture-only-not-inspected")
    pytester.makepyfile(
        test_coding_docker_integration=(
            "import pytest\ndef test_effect(): pytest.skip('prerequisite unavailable')"
        )
    )
    result = _run_gate(
        pytester, monkeypatch, "-p", "scripts.ci_support.pytest_gates", "--integration-gates=docker"
    )
    result.assert_outcomes(skipped=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
