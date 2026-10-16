import json
import sqlite3

import pytest
from typer.testing import CliRunner

from Sprout.cli.commands.evolution import app
from Sprout.storage.local.sqlite.verification_report import verification_report


def database(tmp_path, state="uncertain"):
    path = tmp_path / "metadata.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE verification_executions "
            "(id TEXT, task_id TEXT, state TEXT, record_json TEXT, result_json TEXT)"
        )
        connection.execute(
            "INSERT INTO verification_executions VALUES (?, ?, ?, ?, ?)",
            ("run-1", "task-1", state, json.dumps({
                "signature": "fingerprint", "owner": "private-owner",
                "command": ["secret-argument"], "container_name": "sprout-verification-example",
                "input_manifest": {"private-filename": "hash"},
            }), json.dumps({"reason": "verification_result_unconfirmed",
                            "process": {"stdout": "private-log"}})),
        )
    return path


def test_report_is_readonly_and_redacted(tmp_path):
    path = database(tmp_path)
    before = path.read_bytes()
    report = verification_report(path)
    assert path.read_bytes() == before
    assert report["executions"][0]["requires_manual_review"] is True
    assert report["executions"][0]["retry_authorized"] is False
    assert report["executions"][0]["input_file_count"] == 1
    assert "private" not in json.dumps(report)
    assert "secret-argument" not in json.dumps(report)
    assert verification_report(path, "missing")["executions"] == []


def test_missing_database_never_created(tmp_path):
    path = tmp_path / "missing" / "metadata.db"
    with pytest.raises(ValueError):
        verification_report(path)
    assert not path.parent.exists()


def test_old_database_never_migrated(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path):
        pass
    before = path.read_bytes()
    with pytest.raises(ValueError):
        verification_report(path)
    assert path.read_bytes() == before


def test_malformed_evidence_fails_closed(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE verification_executions SET record_json='[]'")
    with pytest.raises(ValueError):
        verification_report(path)


def test_cli_without_runtime(tmp_path):
    path = database(tmp_path, "completed")
    result = CliRunner().invoke(app, ["verification-report", "--database", str(path)])
    assert result.exit_code == 0
    item = json.loads(result.stdout)["executions"][0]
    assert item["requires_manual_review"] is False
    assert item["trusted_execution"] is False
    assert item["retry_authorized"] is False
