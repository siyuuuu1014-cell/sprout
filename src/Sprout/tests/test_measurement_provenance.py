import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest

from Sprout.evolution.measurement_provenance import provenance_blockers
from Sprout.evolution.pytest_measurement import summarize


def fixture(tmp_path, state="completed", version=2):
    task = "task"
    identity = str(uuid5(NAMESPACE_URL, task + ":verification:test-0"))
    nonce = uuid5(NAMESPACE_URL, task + ":test-0").hex
    payload = {"schema_version": 1, "nonce": nonce, "exit_code": 0,
               "runner": {"probe_hash": "a" * 64}, "collected": ["test.py::test_ok"],
               "reports": [{"nodeid": "test.py::test_ok", "phase": phase, "outcome": "passed"}
                           for phase in ("setup", "call", "teardown")]}
    summary = summarize(payload, nonce, 0, "a" * 64)
    command = ["docker", "run", "image", "python", "-", nonce, "test.py"]
    process = {"command": command, "allowed": True, "exit_code": 0,
               "stdout": "SPROUT_PYTEST_REPORT " + nonce + " " + json.dumps(payload)}
    record = {"id": identity, "task_id": task, "slot": "test-0", "schema_version": version,
              "nonce": nonce, "observer_hash": "a" * 64, "command": command,
              "container_name": "container"}
    output = {"execution_id": identity, "process": process, "waiting": False,
              "pytest_observation": summary, "container_name": "container", "input_unchanged": True}
    database = tmp_path / "metadata.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE verification_executions "
                           "(id TEXT, task_id TEXT, state TEXT, "
                           "record_json TEXT, result_json TEXT)")
        connection.execute("INSERT INTO verification_executions VALUES (?,?,?,?,?)",
                           (identity, task, state, json.dumps(record), json.dumps(output)))
    observed = {**process, "command": ["pytest", "test.py"], "executed_command": command,
                "kind": "test", "execution_id": identity, "verification_image": "image",
                "container_name": "container", "input_unchanged": True,
                "pytest_observation": summary}
    return SimpleNamespace(task_id=task, evidence_dir=str(tmp_path)), {"records": [observed]}


def test_persisted_observation_recomputed_read_only(tmp_path):
    result, report = fixture(tmp_path)
    before = (tmp_path / "metadata.db").read_bytes()
    assert provenance_blockers([result], [report]) == []
    assert (tmp_path / "metadata.db").read_bytes() == before
    assert report["records"][0]["pytest_observation"]["trusted_execution"] is False


@pytest.mark.parametrize("field,value", [("exit_code", 1), ("stdout", "forged"),
                                         ("execution_id", "other"), ("input_unchanged", False)])
def test_import_changes_cannot_override_execution(tmp_path, field, value):
    result, report = fixture(tmp_path)
    changed = deepcopy(report)
    changed["records"][0][field] = value
    assert provenance_blockers([result], [changed]) == ["execution_provenance_binding_mismatch"]


@pytest.mark.parametrize("state", ["active", "uncertain"])
def test_unknown_execution_blocks(tmp_path, state):
    result, report = fixture(tmp_path, state=state)
    assert provenance_blockers([result], [report]) == ["execution_provenance_not_completed"]


def test_old_records_not_backfilled(tmp_path):
    result, report = fixture(tmp_path, version=1)
    assert provenance_blockers([result], [report]) == ["execution_provenance_version_unsupported"]


def test_matching_forged_summaries_fail_raw_output_recomputation(tmp_path):
    result, report = fixture(tmp_path)
    report["records"][0]["pytest_observation"]["collected_count"] = 99
    with sqlite3.connect(tmp_path / "metadata.db") as connection:
        output = json.loads(connection.execute(
            "SELECT result_json FROM verification_executions").fetchone()[0])
        output["pytest_observation"]["collected_count"] = 99
        connection.execute("UPDATE verification_executions SET result_json=?",
                           (json.dumps(output),))
    assert provenance_blockers([result], [report]) == ["execution_provenance_binding_mismatch"]
