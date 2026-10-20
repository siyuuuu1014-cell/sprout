"""R01-DIAG: simulated failures, strict disclosure boundaries, no live services."""

import json
from types import SimpleNamespace

import pytest

from scripts import ci_baseline
from scripts.ci_support import diagnostics as diag

SECRET = "SIMULATED-API-KEY-do-not-publish"
BODY = "PRIVATE-SYSTEM-PROMPT-FILE-CONTENT"


def evidence():
    return {
        "task": {"status": "failed", "instruction": BODY, "metadata": {"api_key": SECRET}},
        "nodes": [
            {
                "type": "evaluation",
                "status": "completed",
                "attempts": 1,
                "metadata": {"output": {"status": "failed", "dialogue": BODY}},
            },
            {
                "type": "apply",
                "status": "failed",
                "attempts": 1,
                "metadata": {
                    "error": "Apply did not land change proposal: patch does not apply " + SECRET,
                    "output": {"applied": False, "diff": BODY},
                },
            },
        ],
        "verification": {
            "verified": False,
            "blocker": SECRET,
            "failure_codes": ["runner_error"],
            "checks": [
                {
                    "name": "private " + SECRET,
                    "command": ["pytest", SECRET],
                    "executed": True,
                    "passed": False,
                    "outcome": "runner_error",
                    "exit_code": 2,
                    "cwd": SECRET,
                    "output": "ModuleNotFoundError: " + SECRET + BODY,
                },
                {
                    "name": "poc: " + SECRET,
                    "executed": False,
                    "passed": False,
                    "outcome": "approval_required",
                    "approval_id": SECRET,
                },
            ],
        },
        "approvals": [{"status": "approved", "params": {"token": SECRET}}],
        "proposals": [{"status": "approved", "diffs": BODY, "metadata": {"dsn": SECRET}}],
        "before": {"head": "a" * 40, "commits": 1},
        "after": {"head": "a" * 40, "commits": 1},
    }


def test_projection_explains_simulated_failures_without_disclosing_raw_fields():
    summary = diag.project("e2e_proposal", **evidence())
    text = json.dumps(summary)
    assert SECRET not in text and BODY not in text
    assert summary["task_status"] == "failed"
    checks = summary["verification"]["checks"]
    assert checks[0]["name"] == "pytest" and checks[0]["exit_code"] == 2
    assert checks[0]["signals"] == ["module_missing"]
    assert checks[1]["name"] == "poc" and checks[1]["status"] == "not_run"
    assert summary["first_failure"] == {
        "node_index": 0,
        "check_index": 0,
        "ordering": "record_order_not_wall_clock",
    }
    assert summary["apply"]["succeeded"] is False
    assert "git_patch_rejected" in summary["apply"]["reason_categories"]
    assert summary["git"] == {
        "head_changed": False,
        "before_count": 1,
        "actual_count": 1,
        "expected_minimum_count": 2,
    }
    assert summary["root_cause"] == "not_established"


@pytest.mark.parametrize("field", ["task", "nodes", "verification", "approvals", "proposals"])
def test_unknown_enum_text_is_not_published(field):
    raw = evidence()
    if field == "task":
        raw[field]["status"] = SECRET
    elif field == "nodes":
        raw[field][0].update(type=SECRET, status=SECRET)
    elif field == "verification":
        raw[field]["failure_codes"] = [SECRET]
        raw[field]["checks"][0].update(name=SECRET, outcome=SECRET, exit_code=SECRET)
    else:
        raw[field][0]["status"] = SECRET
    assert SECRET not in json.dumps(diag.project("e2e_proposal", **raw))


def test_unknown_label_cannot_be_used_as_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("SPROUT_TEST_DIAGNOSTICS", str(tmp_path))
    with pytest.raises(ValueError, match="Unknown diagnostic"):
        diag.project("../" + SECRET)
    assert not list(tmp_path.iterdir())


def test_missing_evidence_is_unknown_not_success():
    summary = diag.project("eval_C01", missing=("task", "verification", SECRET))
    assert summary["task_status"] == "unknown"
    assert summary["verification"]["verified"] is None
    assert summary["apply"]["execution_confirmed"] is None
    assert summary["apply"]["succeeded"] is None
    assert summary["git"]["head_changed"] is None
    assert summary["missing"] == ["task", "verification"]


def test_evaluation_reuses_existing_json_and_exports_only_whitelist(tmp_path, monkeypatch):
    directory = tmp_path / "eval" / "C01"
    directory.mkdir(parents=True)
    raw = evidence()
    for key in ("task", "nodes", "verification", "approvals", "proposals"):
        (directory / (key + ".json")).write_text(json.dumps(raw[key]), encoding="utf-8")
    (directory / "baseline.json").write_text(json.dumps({"revision": "a" * 40}))
    (directory / ".env").write_text(SECRET)
    (directory / "metadata.db").write_bytes(BODY.encode())
    public = tmp_path / "public"
    monkeypatch.setenv("SPROUT_TEST_DIAGNOSTICS", str(public))
    monkeypatch.setattr(diag, "_git", lambda *args: "1")
    monkeypatch.setattr(diag, "git_state", lambda *args: raw["after"])
    diag.evaluation_failures(
        {
            "results": [
                {"id": "C01", "scenario_passed": False, "evidence_dir": str(directory)},
                {"id": "C02", "scenario_passed": True, "evidence_dir": str(directory)},
            ]
        },
        tmp_path,
    )
    assert [path.name for path in public.iterdir()] == ["eval_C01.json"]
    text = (public / "eval_C01.json").read_text()
    assert SECRET not in text and BODY not in text
    assert json.loads(text)["verification"]["checks"][0]["exit_code"] == 2
    assert (directory / ".env").read_text() == SECRET
    assert (directory / "metadata.db").read_bytes() == BODY.encode()


def test_evaluation_rejects_evidence_outside_test_directory(tmp_path):
    with pytest.raises(ValueError, match="outside test"):
        diag.evaluation_failures(
            {
                "results": [
                    {
                        "id": "C01",
                        "scenario_passed": False,
                        "evidence_dir": str(tmp_path.parent),
                    }
                ]
            },
            tmp_path,
        )


async def test_runtime_capture_uses_read_only_queries_and_filters_task(tmp_path, monkeypatch):
    raw = evidence()

    async def task(_id):
        return SimpleNamespace(**raw["task"])

    async def nodes(_id):
        return raw["nodes"]

    async def verification(_id):
        return raw["verification"]

    async def approvals():
        return [
            {"task_id": "fixture", "status": "approved", "params": SECRET},
            {"task_id": "foreign", "status": "rejected", "params": BODY},
        ]

    async def proposals(_id):
        return raw["proposals"]

    runtime = SimpleNamespace(
        get_task=task,
        get_verification_report=verification,
        list_approvals=approvals,
        list_change_proposals=proposals,
        storage=SimpleNamespace(metadata=SimpleNamespace(list_execution_nodes=nodes)),
    )
    monkeypatch.setattr(diag, "git_state", lambda repo: raw["after"])
    monkeypatch.setenv("SPROUT_TEST_DIAGNOSTICS", str(tmp_path))
    await diag.runtime_failure(runtime, "fixture", tmp_path, raw["before"], "e2e_delete_approve")
    summary = json.loads((tmp_path / "e2e_delete_approve.json").read_text())
    assert summary["approval_states"] == ["approved"]
    assert SECRET not in json.dumps(summary) and BODY not in json.dumps(summary)


def test_public_baseline_aggregates_do_not_copy_raw_junit_or_logs(tmp_path):
    private_path = "/home/private-user/history.sqlite"
    (tmp_path / "l1.xml").write_text(
        '<testsuites><testsuite tests="2" failures="1" errors="0" skipped="0">'
        f'<testcase name="{SECRET}" file="{private_path}">'
        f'<failure message="{SECRET}">{BODY} {private_path}</failure>'
        f'<system-out>{SECRET}</system-out><system-err>{BODY}</system-err></testcase>'
        "</testsuite></testsuites>"
    )
    (tmp_path / "raw.log").write_text(SECRET + BODY)
    (tmp_path / "l1-gates.json").write_text(
        json.dumps(
            {
                "tests": [
                    {"nodeid": SECRET, "status": "not_run", "reason": BODY},
                ]
            }
        )
    )
    ci_baseline.public_summary(
        tmp_path,
        {
            "steps": [
                {
                    "name": "l1",
                    "exit_code": 1,
                    "command": [SECRET],
                    "error": BODY,
                }
            ]
        },
    )
    public = tmp_path / "public-diagnostics"
    assert {path.name for path in public.iterdir()} == {"baseline.json", "l1-failures.json"}
    for path in public.iterdir():
        projected = path.read_text()
        assert SECRET not in projected and BODY not in projected
        assert private_path not in projected
        assert all(raw not in projected for raw in (
            "<testsuite", "<failure", "system-out", "system-err",
        ))
    failures = json.loads((public / "l1-failures.json").read_text())
    assert failures["counts"] == {"tests": 2, "failures": 1, "errors": 0}
    assert failures["cases"] == [{
        "test_id": None, "case_ordinal": None, "source": None,
        "exception_class": None, "category": "test_failed",
    }]
    text = (public / "baseline.json").read_text()
    assert SECRET not in text and BODY not in text
    assert json.loads(text)["tests"]["l1"]["failures"] == 1
    assert json.loads(text)["inventory_counts"]["l1"] == {"total": 1, "not_run": 1}


def test_workflow_uploads_only_projected_json():
    workflow = (ci_baseline.ROOT / ".github/workflows/baseline.yml").read_text()
    assert "path: .workbuddy/ci-baseline/public-diagnostics/*.json" in workflow
    assert "ci-baseline/*.log" not in workflow
    assert "ci-baseline/*.xml" not in workflow


async def test_query_errors_publish_missing_evidence_not_exception_text(tmp_path, monkeypatch):
    async def broken(*args):
        raise RuntimeError(SECRET + BODY)

    runtime = SimpleNamespace(
        get_task=broken,
        get_verification_report=broken,
        list_approvals=broken,
        list_change_proposals=broken,
        storage=SimpleNamespace(metadata=SimpleNamespace(list_execution_nodes=broken)),
    )
    monkeypatch.setenv("SPROUT_TEST_DIAGNOSTICS", str(tmp_path))
    monkeypatch.setattr(diag, "git_state", lambda repo: {"head": None, "commits": None})
    await diag.runtime_failure(runtime, "fixture", tmp_path, {}, "e2e_proposal")
    summary = json.loads((tmp_path / "e2e_proposal.json").read_text())
    assert summary["missing"] == ["task", "nodes", "verification", "approvals", "proposals"]
    assert summary["task_status"] == "unknown"
    assert SECRET not in json.dumps(summary) and BODY not in json.dumps(summary)


def test_versions_do_not_copy_arbitrary_version_output(monkeypatch):
    monkeypatch.setattr(diag, "_git", lambda *args: SECRET)
    monkeypatch.setattr(diag.importlib.metadata, "version", lambda *args: BODY)
    data = diag.environment()
    assert data["git"] == data["ruff"] == "unknown"
    assert SECRET not in json.dumps(data) and BODY not in json.dumps(data)
