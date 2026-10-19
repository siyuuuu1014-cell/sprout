"""Explicit infrastructure classification, not a list of tests to hide failures.

All tests are collected first. L1 excludes only reviewed external-service tests
and records each as not_run. Existing skip reasons in selected tests stay visible.
"""

import json
import os
from pathlib import Path

import pytest

_MODULES = {
    "test_coding_docker_integration.py": ("docker",),
    "test_coding_graph_docker_integration.py": ("docker",),
    "test_cart_properties_docker.py": ("docker",),
    "test_docker_verification_integration.py": ("docker",),
    "test_replay_resume_docker_integration.py": ("docker",),
    "test_validation_handoff_docker_integration.py": ("docker",),
    "test_temporal_integration.py": ("temporal",),
    "test_temporal_baseline_replay.py": ("temporal",),
    "test_model_budget_temporal_integration.py": ("temporal",),
    "test_observation_process_integration.py": ("temporal",),
    "test_gateway_process_integration.py": ("temporal",),
    "test_detached_coding_cli_integration.py": ("docker", "temporal"),
    "test_evolution_cli_process_integration.py": ("docker", "temporal"),
    "test_isolated_coding_temporal_integration.py": ("docker", "temporal"),
    "test_controlled_growth_demo.py": ("docker", "temporal"),
    "test_deepseek_live_coding.py": ("docker", "temporal", "model"),
}
_FUNCTIONS = {
    ("test_replay_functions.py", "test_real_function_container_and_durable_cache"): ("docker",),
    (
        "test_replay_coding_entry.py",
        "test_replay_runner_uses_dedicated_worker_without_generic_start",
    ): ("docker", "temporal"),
}


def gates_for(item):
    filename = item.path.name
    name = item.originalname or item.name.split("[")[0]
    gates = set(_MODULES.get(filename, _FUNCTIONS.get((filename, name), ())))
    # These modules mix offline contracts with explicitly real-service tests.
    if filename == "test_isolated_worker.py" and "real" in name:
        gates.add("temporal")
    if filename == "test_sixlane.py" and "live" in name:
        gates.add("databases")
    if filename == "test_gateway_process_integration.py":
        if getattr(item, "callspec", None) and item.callspec.params.get("worker_mode") == (
            "full-cli-code-live"
        ):
            gates.add("model")
    if filename == "test_temporal_integration.py":
        mode = getattr(item, "callspec", None)
        if mode and "outage" in str(mode.params.get("mode", "")):
            gates.add("docker_faults")
    return sorted(gates)


def pytest_addoption(parser):
    parser.addoption(
        "--baseline-l1", action="store_true", help="offline tests; report external not_run"
    )
    parser.addoption("--integration-gates", default="", help="explicit gates, comma separated")
    parser.addoption("--gate-report", help="JSON inventory output path")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "external_service: requires explicitly enabled infrastructure"
    )
    config._baseline_inventory = []


def pytest_collection_modifyitems(config, items):
    requested = set(filter(None, config.getoption("--integration-gates").split(",")))
    known = {"docker", "temporal", "model", "databases", "docker_faults"}
    if requested - known or (requested and config.getoption("--baseline-l1")):
        raise pytest.UsageError("invalid or conflicting baseline integration gates")
    selected, excluded = [], []
    for item in items:
        gates = gates_for(item)
        if gates:
            item.add_marker(pytest.mark.external_service)
        include = not gates if config.getoption("--baseline-l1") else True
        if requested:
            include = bool(gates) and set(gates) <= requested
        config._baseline_inventory.append(
            {
                "nodeid": item.nodeid,
                "gates": gates,
                "status": "selected" if include else "not_run",
                "reason": "explicit gate selection",
            }
        )
        (selected if include else excluded).append(item)
    if excluded:
        config.hook.pytest_deselected(items=excluded)
        items[:] = selected
    if requested and not items:
        raise pytest.UsageError("no tests match enabled integration gates")
    if requested:
        required = {
            "temporal": "SPROUT_TEMPORAL_TEST_HOST",
            "model": "SPROUT_LIVE_REQUEST_LIMIT",
            "docker_faults": "SPROUT_TEMPORAL_DOCKER_FAULTS",
        }
        missing = [
            value
            for gate, value in required.items()
            if gate in requested and not os.environ.get(value)
        ]
        if "docker" in requested and not (
            os.environ.get("SPROUT_CODING_TEST_IMAGE")
            or os.environ.get("SPROUT_VERIFICATION_TEST_IMAGE")
        ):
            missing.append("explicit pinned local test image")
        if missing:
            raise pytest.UsageError("integration BLOCKED: missing " + ", ".join(missing))
        if "temporal" in requested and os.environ["SPROUT_TEMPORAL_TEST_HOST"] != "127.0.0.1:17233":
            raise pytest.UsageError("integration BLOCKED: only dedicated 127.0.0.1:17233 permitted")
        if "docker_faults" in requested and os.environ["SPROUT_TEMPORAL_DOCKER_FAULTS"] != "1":
            raise pytest.UsageError(
                "integration BLOCKED: fault injection must be explicitly enabled"
            )
        if "databases" in requested and not all(
            os.environ.get(f"SPROUT_TEST_{key}_URL") for key in ("REDIS", "MILVUS", "NEO4J")
        ):
            raise pytest.UsageError(
                "integration BLOCKED: explicit dedicated database URLs required"
            )
        if "model" in requested:
            try:
                limit = int(os.environ["SPROUT_LIVE_REQUEST_LIMIT"])
            except ValueError as error:
                raise pytest.UsageError(
                    "integration BLOCKED: invalid model request budget"
                ) from error
            if not 1 <= limit <= 20 or not os.environ.get("SPROUT_LIVE_CONFIG"):
                raise pytest.UsageError(
                    "integration BLOCKED: model config and budget 1..20 required"
                )


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    if session.config.getoption("--integration-gates") and reporter.stats.get("skipped"):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    target = session.config.getoption("--gate-report")
    if target:
        Path(target).write_text(
            json.dumps(
                {
                    "exitstatus": int(session.exitstatus),
                    "tests": session.config._baseline_inventory,
                    "integration": "not_run"
                    if session.config.getoption("--baseline-l1")
                    else "see JUnit",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
