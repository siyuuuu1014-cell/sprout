"""Synthetic JUnit only; no real service, credentials or user database."""

import json
import xml.etree.ElementTree as ET

import pytest

from scripts.ci_support.junit_failures import failure_summary


def sample(tmp_path, *, classname="src.Sprout.tests.test_safe", name="test_boundary"):
    folder = tmp_path / "src/Sprout/tests"
    folder.mkdir(parents=True)
    (folder / "test_safe.py").write_text("def test_boundary():\n    pass\n", encoding="utf-8")
    suite = ET.Element("testsuite", tests="3", failures="1", errors="1")
    for outcome in ("failure", "error", None):
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if outcome:
            failure = ET.SubElement(case, outcome, type="AssertionError", message="SECRET-TOKEN")
            failure.text = "C:/Users/private/history.sqlite\nSECRET-TOKEN\nraw traceback"
        ET.SubElement(case, "system-out").text = "API_KEY=SECRET-TOKEN"
    path = tmp_path / "l1.xml"
    ET.ElementTree(suite).write(path, encoding="utf-8")
    return path


@pytest.mark.parametrize("suffix", ["", "[SECRET-TOKEN]", "[/home/private/key]", "[a\nb]"])
def test_projection_counts_and_opaque_parameter_ordinals(tmp_path, suffix):
    result = failure_summary(sample(tmp_path, name="test_boundary" + suffix), tmp_path)
    assert result["status"] == "ok"
    assert result["counts"] == {"tests": 3, "failures": 1, "errors": 1}
    assert [r["case_ordinal"] for r in result["cases"]] == [1, 2]
    for record in result["cases"]:
        assert set(record) == {"test_id", "case_ordinal", "source", "exception_class", "category"}
        assert record["test_id"] == "src.Sprout.tests.test_safe::test_boundary"
        assert record["source"] == {
            "path": "src/Sprout/tests/test_safe.py", "function": "test_boundary", "line": 1,
        }
    encoded = json.dumps(result)
    for sensitive in ("SECRET-TOKEN", "/home/private", "C:/Users", "history.sqlite", "traceback"):
        assert sensitive not in encoded


@pytest.mark.parametrize("classname,name", [
    ("/home/private/SECRET-TOKEN", "test_boundary"),
    ("src.Sprout.tests.test_safe", "test_invented_SECRET_TOKEN"),
    ("src.Sprout.tests.test_safe", "x" * 5000),
])
def test_unproven_identity_and_exception_are_null(tmp_path, classname, name):
    path = sample(tmp_path, classname=classname, name=name)
    tree = ET.parse(path)
    for item in tree.getroot().iter("failure"):
        item.set("type", "SECRET-TOKEN")
    tree.write(path, encoding="utf-8")
    result = failure_summary(path, tmp_path)
    assert result["counts"]["failures"] == 1
    assert result["cases"][0]["exception_class"] is None
    assert all(r["test_id"] is None and r["source"] is None for r in result["cases"])
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("raw", [
    '<!DOCTYPE x [<!ENTITY key "SECRET-TOKEN">]><testsuite/>',
    '<testsuite tests="SECRET-TOKEN"/>', '<bad>SECRET-TOKEN', '<private/>',
])
def test_malformed_xml_has_only_fixed_error(tmp_path, raw):
    path = tmp_path / "l1.xml"
    path.write_text(raw, encoding="utf-8")
    assert failure_summary(path, tmp_path) == {
        "schema_version": 1, "status": "unreadable", "counts": None, "cases": [],
    }


def test_count_mismatch_is_not_reported_as_complete(tmp_path):
    path = sample(tmp_path)
    tree = ET.parse(path)
    tree.getroot().set("failures", "2")
    tree.write(path, encoding="utf-8")
    result = failure_summary(path, tmp_path)
    assert result["status"] == "count_mismatch"
    assert result["counts"]["failures"] == 2


def test_public_summary_writes_only_safe_projection(tmp_path, monkeypatch):
    from scripts import ci_baseline

    sample(tmp_path)
    monkeypatch.setattr(ci_baseline, "ROOT", tmp_path)
    ci_baseline.public_summary(tmp_path, {"steps": [{"name": "l1", "exit_code": 1}]})
    report = json.loads((tmp_path / "public-diagnostics/l1-failures.json").read_text())
    assert report["counts"]["failures"] == 1
    assert "SECRET" not in json.dumps(report)
