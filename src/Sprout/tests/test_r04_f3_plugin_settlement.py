"""Plugin diagnostics cannot create a trusted pytest verdict."""

import json

import pytest

from Sprout.execution.verification_run import DockerVerificationRun, _UnverifiedPytest
from Sprout.tests.test_r04_f3_adapter import inspection, observation, plan
from Sprout.tests.test_r04_f3_manifest import execution as execution
from Sprout.tests.test_r04_f3_manifest import f3 as f3
from Sprout.tests.test_r04_f3_manifest import rig as rig
from Sprout.tests.test_r04_review_only_proposal import graph as graph


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("identity", ["valid", "nonce", "probe"])
def test_plugin_mismatch_requires_confirmed_container(running, identity):
    value = plan()
    value["request"].update(
        argv=["pytest"],
        pytest_observer_sha="a" * 64,
        pytest_contract={"plugins": [], "files": ["test_app.py"], "ini_file": None, "configs": {}},
    )
    line = observation(value)
    marker, encoded = line.split(" ", 2)[0:2], line.split(" ", 2)[2]
    payload = json.loads(encoded)
    payload["stdout"] = (
        "SPROUT_PYTEST_REPORT "
        + value["nonce"]
        + " "
        + json.dumps(
            {
                "configuration": {"plugins": ["unexpected"], "relative_config": None},
                "trusted_execution": True,
                "nonce": value["nonce"] if identity != "nonce" else "wrong",
                "runner": {"probe_hash": "a" * 64 if identity != "probe" else "wrong"},
                "exit_code": 0,
                "collected": ["test_app.py::test_one"],
                "collection_errors": [],
                "reports": [
                    {"nodeid": "test_app.py::test_one", "phase": "call", "outcome": "passed"}
                ],
            }
        )
    )
    line = " ".join(marker) + " " + json.dumps(payload)
    state = inspection(value)
    if running or identity != "valid":
        if running:
            state["State"].update(Running=True, Pid=42, Status="running")
        with pytest.raises(ValueError) as error:
            DockerVerificationRun.command_observation(value, line, 0, state)
        assert not isinstance(error.value, _UnverifiedPytest)
    else:
        with pytest.raises(_UnverifiedPytest) as error:
            DockerVerificationRun.command_observation(value, line, 0, state)
        assert error.value.evidence["outcome"] == "unverified"
        assert error.value.evidence["passed"] is False
        assert error.value.evidence["reason"] == "verification_pytest_configuration_unproved"


async def test_plugin_mismatch_settles_through_real_review_graph(graph, monkeypatch):
    from Sprout.execution.process_broker import ProcessBroker
    from Sprout.tests.test_r04_f3_adapter import exercise_owned_verification

    monkeypatch.setattr(
        graph.broker,
        "execute_verification_obligation",
        ProcessBroker.execute_verification_obligation.__get__(graph.broker),
    )
    await exercise_owned_verification(graph.e, monkeypatch, "success", graph=graph)
