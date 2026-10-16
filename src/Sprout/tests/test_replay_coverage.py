from copy import deepcopy
from hashlib import sha256

import pytest

from Sprout.evolution.replay_coverage import acceptance_coverage, compare_structured
from Sprout.execution.acceptance import render_acceptance_tests
from Sprout.strategy.acceptance import AcceptanceSpec


def report_fixture():
    spec = AcceptanceSpec.from_payload(
        {"cases": [{"id": "normal", "target": "app:add", "args": [1, 2], "expected_output": 3}]}
    )
    node = "tests/test_acceptance.py::test_normal"
    observation = {
        "nodeids": [node],
        "executed_nodeids": [node],
        "runner": {
            key: "identity"
            for key in ("python", "executable", "pytest_version", "pytest_module", "probe_hash")
        },
        "configuration": {
            "plugins": ["sprout_growth_observer"],
            "inipath": None,
            "relative_config": None,
            "config_hash": None,
        },
    }
    return {
        "acceptance_spec": spec.to_payload(),
        "oracle_snapshot": {
            "tests/test_acceptance.py": sha256(render_acceptance_tests(spec).encode()).hexdigest()
        },
        "records": [{"pytest_observation": observation}],
    }


def test_same_observed_tests_and_frozen_acceptance_are_comparable_not_trusted():
    result = compare_structured(report_fixture(), report_fixture())
    assert result["comparable"]
    assert not result["trusted_execution"]
    assert all(item["covered"] for item in result["acceptance_coverage"])


@pytest.mark.parametrize(
    "problem", ["tests", "runner", "plugins", "config", "oracle", "spec", "skipped"]
)
def test_structured_mismatches_block_comparison(problem):
    left = report_fixture()
    right = deepcopy(left)
    observation = right["records"][0]["pytest_observation"]
    if problem == "tests":
        observation["nodeids"] = ["tests/test_fake.py::test_ok"]
    elif problem == "runner":
        observation["runner"]["pytest_version"] = "changed"
    elif problem == "plugins":
        observation["configuration"]["plugins"] = ["different-plugin"]
    elif problem == "config":
        observation["configuration"].update(inipath="/external/pytest.ini", config_hash="hash")
    elif problem == "oracle":
        right["oracle_snapshot"]["tests/test_acceptance.py"] = "changed"
    elif problem == "spec":
        right["acceptance_spec"]["cases"][0]["expected_output"] = 4
    else:
        observation["executed_nodeids"] = []
    assert not compare_structured(left, right)["comparable"]


def test_same_function_name_in_an_unfrozen_file_does_not_prove_coverage():
    report = report_fixture()
    report["records"][0]["pytest_observation"]["executed_nodeids"] = [
        "tests/test_fake.py::test_normal"
    ]
    assert not acceptance_coverage(report)["covered"]


def test_missing_evidence_is_waiting_not_coverage():
    assert not compare_structured(None, None)["comparable"]
    assert not acceptance_coverage({})["covered"]


@pytest.mark.parametrize("problem", ["image", "input"])
def test_docker_image_or_export_changes_are_not_comparable(problem):
    left = report_fixture()
    left["records"][0].update(verification_image="sha256:image", input_unchanged=True)
    right = deepcopy(left)
    if problem == "image":
        right["records"][0]["verification_image"] = "sha256:different"
    else:
        right["records"][0]["input_unchanged"] = False
    assert not compare_structured(left, right)["comparable"]


def test_real_independent_pytest_runs_have_same_collection_and_acceptance(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from Sprout.evolution import pytest_probe
    from Sprout.evolution.pytest_measurement import summarize

    source = report_fixture()
    spec = AcceptanceSpec.from_payload(source["acceptance_spec"])
    reports = []
    for name in ("baseline", "candidate"):
        repo = tmp_path / name
        (repo / "tests").mkdir(parents=True)
        (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (repo / "tests" / "test_acceptance.py").write_text(
            render_acceptance_tests(spec), encoding="utf-8", newline="\n"
        )
        (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8", newline="\n")
        output = tmp_path / (name + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                str(Path(pytest_probe.__file__).resolve()),
                str(output),
                name,
                "-q",
                "-p",
                "no:cacheprovider",
                "tests/test_acceptance.py",
            ],
            cwd=repo,
            capture_output=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        payload = json.loads(output.read_text(encoding="utf-8"))
        observation = summarize(payload, name, 0, payload["runner"]["probe_hash"])
        assert observation["observed_all_passed"]
        report = deepcopy(source)
        report["oracle_snapshot"]["pytest.ini"] = sha256(
            (repo / "pytest.ini").read_bytes()
        ).hexdigest()
        report["records"][0]["pytest_observation"] = observation
        reports.append(report)
    result = compare_structured(*reports)
    assert result["comparable"], result["blockers"]
    assert not result["trusted_execution"]
