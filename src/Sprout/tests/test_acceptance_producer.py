import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.acceptance_producer import checked_bundle, produce
from Sprout.workspace.authorized_content import AuthorizedEntry
from Sprout.workspace.models import ResourceKind


def inputs():
    entries = (
        AuthorizedEntry.synthetic("maths.py", b"def double(n): return n * 3"),
        AuthorizedEntry.synthetic(
            "SPEC.md", b"Return twice the input. Zero returns zero.",
            ResourceKind.DOCUMENTATION,
        ),
    )
    plan = {"reads": ["maths.py", "SPEC.md"], "writes": ["maths.py"],
            "execution": ["run_tests"], "actions": ["code_modification"]}
    normal = {
        "id": "positive", "target": "maths:double", "args": [3],
        "expected_output": 6, "category": "normal",
        "evidence": {"path": "SPEC.md", "quote": "Return twice the input."},
    }
    zero = {**normal, "id": "zero", "args": [0], "expected_output": 0,
            "category": "boundary", "evidence": {"path": "SPEC.md", "quote": "Zero returns zero."}}
    design = {"acceptance": {"cases": [normal, zero]}, "regression": {
        "cases": [{**normal, "id": "retained_positive", "args": [2], "expected_output": 4}],
    }}
    return entries, plan, design


@pytest.mark.parametrize("invalid", [None, "json", "target", "empty", "placeholder", "quote",
                                    "duplicate", "boundary"])
async def test_producer_freezes_only_grounded_cases(invalid):
    entries, plan, design = inputs()
    case = design["acceptance"]["cases"][0]
    if invalid == "target":
        case["target"] = "maths:missing"
    if invalid == "empty":
        design["regression"]["cases"] = []
    if invalid == "placeholder":
        case["expected_output"] = "TODO"
    if invalid == "quote":
        case["evidence"]["quote"] = "Uncaptured requirement"
    if invalid == "duplicate":
        design["regression"]["cases"][0]["id"] = case["id"]
    if invalid == "boundary":
        design["acceptance"]["cases"].pop()
    reply = "malformed" if invalid == "json" else json.dumps(design)
    model = SimpleNamespace(chat=AsyncMock(return_value=SimpleNamespace(text=reply)))
    if invalid:
        with pytest.raises(ValueError, match="acceptance_"):
            await produce(model, "Fix double.", plan, entries)
        return
    bundle = await produce(model, "Fix double.", plan, entries)
    checked_bundle(bundle)
    assert bundle["acceptance_sha256"] != bundle["regression_sha256"]
    assert bundle["acceptance_spec"]["cases"][0]["expected_output"] == 6
    messages = model.chat.call_args.args[0]
    assert "return n * 3" not in messages[-1].content
    assert model.chat.call_args.kwargs["tools"] == ()
    bundle["acceptance_spec"]["cases"][0]["expected_output"] = 9
    with pytest.raises(ValueError, match="acceptance_bundle_changed"):
        checked_bundle(bundle)



async def test_feature_acceptance_can_exercise_new_parameter_before_coding():
    entries, plan, design = inputs()
    plan["actions"].append("feature_extension")
    design["acceptance"]["cases"][0]["kwargs"] = {"priority": True}
    model = SimpleNamespace(chat=AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(design)),
    ))
    bundle = await produce(model, "Extend the public function.", plan, entries)
    assert checked_bundle(bundle)[0].cases[0].kwargs == {"priority": True}
    design["regression"]["cases"][0]["kwargs"] = {"priority": True}
    model.chat.return_value = SimpleNamespace(text=json.dumps(design))
    with pytest.raises(ValueError, match="acceptance_argument_binding"):
        await produce(model, "Extend the public function.", plan, entries)


async def test_producer_corrects_an_uncaptured_citation():
    entries, plan, design = inputs()
    citation = design["acceptance"]["cases"][0]["evidence"]
    correct = json.dumps(design)
    citation["quote"] = "This text is absent from the specification."
    model = SimpleNamespace(chat=AsyncMock(side_effect=[
        SimpleNamespace(text=json.dumps(design)), SimpleNamespace(text=correct),
    ]))
    bundle = await produce(model, "Fix double.", plan, entries)
    assert checked_bundle(bundle)[0].cases[0].expected_output == 6
    assert model.chat.await_count == 2
    assert "acceptance_evidence_not_captured" in model.chat.call_args.args[0][-1].content


async def test_producer_corrects_positional_use_of_keyword_only_argument():
    entries, plan, design = inputs()
    entries = (AuthorizedEntry.synthetic("maths.py", b"def double(n, *, flag=False): return n"),
               entries[1])
    normal = design["acceptance"]["cases"][0]
    normal["args"] = [3, True]
    invalid = json.dumps(design)
    normal["args"] = [3]
    normal["kwargs"] = {"flag": True}
    model = SimpleNamespace(chat=AsyncMock(side_effect=[
        SimpleNamespace(text=invalid), SimpleNamespace(text=json.dumps(design)),
    ]))
    bundle = await produce(model, "Fix double.", plan, entries)
    assert bundle["acceptance_spec"]["cases"][0]["kwargs"] == {"flag": True}
    assert model.chat.await_count == 2
    assert "acceptance_argument_binding" in model.chat.call_args.args[0][-1].content


@pytest.mark.parametrize("citation", ["SPEC.md", "<requirement>"])
async def test_spec_probe_requires_documented_expectations_and_accepts_invalid_edge(citation):
    entries, plan, design = inputs()
    assertion = "assert double(2) == 4"
    entries += (AuthorizedEntry.synthetic(
        "tests/test_maths.py", assertion.encode(), ResourceKind.TEST,
    ),)
    plan["reads"].append("tests/test_maths.py")
    design["regression"]["cases"][0]["evidence"] = {
        "path": "tests/test_maths.py", "quote": assertion,
    }
    edge = design["acceptance"]["cases"][1]
    del edge["expected_output"]
    edge.update(category="invalid", expected_exception="ValueError")
    requirement = "Inspect the project. Zero returns zero."
    edge["evidence"]["path"] = citation
    model = SimpleNamespace(chat=AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(design)),
    ))
    if citation == "<requirement>":
        with pytest.raises(ValueError, match="acceptance_probe_requires_project_specification"):
            await produce(model, requirement, plan, entries, spec_probe=True)
        return
    bundle = await produce(model, requirement, plan, entries, spec_probe=True)
    acceptance, _ = checked_bundle(bundle)
    assert acceptance.cases[1].expected_exception == "ValueError"


async def test_probe_rejects_composite_only_design_for_documented_callable():
    entries, plan, design = inputs()
    assertion = "assert double(2) == 4"
    entries += (
        AuthorizedEntry.synthetic("fees.py", b"def surcharge(remote=False): return 0"),
        AuthorizedEntry.synthetic("terms.md", b"`surcharge` returns 3 for remote destinations.",
                                  ResourceKind.DOCUMENTATION),
        AuthorizedEntry.synthetic("tests/test_maths.py", assertion.encode(), ResourceKind.TEST),
    )
    plan["reads"].extend(("fees.py", "terms.md", "tests/test_maths.py"))
    plan["writes"].append("fees.py")
    design["regression"]["cases"][0]["evidence"] = {
        "path": "tests/test_maths.py", "quote": assertion,
    }
    model = SimpleNamespace(chat=AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(design)),
    ))
    with pytest.raises(ValueError, match="acceptance_probe_missing_documented_targets.*surcharge"):
        await produce(model, "Check pricing and fix observed failures.", plan, entries,
                      spec_probe=True)


async def test_repair_keeps_directly_failing_leaf_sources_omitted_by_model():
    from Sprout.cli.repair_target import select_spec_repair_targets

    entries, plan, design = inputs()
    assertion = "assert double(2) == 4"
    entries += (
        AuthorizedEntry.synthetic("fees.py", b"def surcharge(remote=False): return 0"),
        AuthorizedEntry.synthetic("quote.py", b"from fees import surcharge"),
        AuthorizedEntry.synthetic("terms.md", b"`surcharge` returns 3 for remote destinations.",
                                  ResourceKind.DOCUMENTATION),
        AuthorizedEntry.synthetic("tests/test_maths.py", assertion.encode(), ResourceKind.TEST),
    )
    plan["reads"].extend(("fees.py", "quote.py", "terms.md", "tests/test_maths.py"))
    plan["writes"].extend(("fees.py", "quote.py"))
    design["regression"]["cases"][0]["evidence"] = {
        "path": "tests/test_maths.py", "quote": assertion,
    }
    design["acceptance"]["cases"].append({
        **design["acceptance"]["cases"][0],
        "id": "remote", "target": "fees:surcharge", "args": [True], "expected_output": 3,
        "evidence": {"path": "terms.md", "quote": "`surcharge` returns 3"},
    })
    model = SimpleNamespace(chat=AsyncMock(side_effect=[
        SimpleNamespace(text=json.dumps(design)), SimpleNamespace(content='{"paths":["maths.py"]}'),
    ]))
    bundle = await produce(model, "Check pricing.", plan, entries, spec_probe=True)
    observation = {"results": [
        {"kind": "acceptance", "case_id": key, "allowed": True, "exit_code": 0,
         "passed": False, "actual_output": 0}
        for key in ("positive", "remote")
    ] + [{"kind": "regression", "allowed": True, "exit_code": 0, "passed": True}]}
    paths = await select_spec_repair_targets(
        model, "Check pricing.", {("maths.py", "quote.py")}, entries, bundle, observation,
    )
    assert paths == ("fees.py", "maths.py")
    assert model.chat.await_count == 2


async def test_bind_probe_plan_keeps_frozen_oracles_when_narrowing_targets():
    from Sprout.execution.acceptance_producer import bind_probe_plan, check_preparation

    entries, plan, design = inputs()
    assertion = "assert double(2) == 4"
    entries += (AuthorizedEntry.synthetic(
        "tests/test_maths.py", assertion.encode(), ResourceKind.TEST,
    ),)
    plan["reads"].append("tests/test_maths.py")
    design["regression"]["cases"][0]["evidence"] = {
        "path": "tests/test_maths.py", "quote": assertion,
    }
    entries += (AuthorizedEntry.synthetic("other.py", b"def add(n): return n + 1"),)
    plan["reads"].append("other.py")
    plan["writes"].append("other.py")
    model = SimpleNamespace(chat=AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(design)),
    ))
    requirement = "Fix double."
    probe = await produce(model, requirement, plan, entries, spec_probe=True)
    observation = {"results": [
        {"kind": "acceptance", "case_id": "positive", "allowed": True, "exit_code": 0,
         "passed": False, "actual_output": 9},
        {"kind": "regression", "allowed": True, "exit_code": 0, "passed": True},
    ]}
    narrowed_plan = {**plan, "writes": ["maths.py"]}
    bound = bind_probe_plan(probe, observation, requirement=requirement, plan=narrowed_plan)
    assert bound["probe_bundle_sha256"] == probe["bundle_sha256"]
    assert check_preparation(bound, requirement, narrowed_plan) == checked_bundle(probe)
    assert set(bound["write_evidence"]) == {"maths.py"}
    for field in ("acceptance_sha256", "regression_sha256", "test_sha256", "demo_sha256"):
        assert bound[field] == probe[field]
    with pytest.raises(ValueError, match="spec_probe_write_target_changed"):
        bind_probe_plan(probe, observation, requirement=requirement,
                        plan={**plan, "writes": ["unrelated.py"]})


async def test_freeze_uses_same_artifacts_for_equivalent_preparation():
    entries, plan, design = inputs()
    model = SimpleNamespace(chat=AsyncMock(
        return_value=SimpleNamespace(text=json.dumps(design)),
    ))
    filesystem = await produce(model, "Fix double.", plan, entries)
    git = await produce(model, "Fix double.", plan, reversed(entries))
    keys = (
        "source_evidence_digest", "acceptance_sha256", "regression_sha256", "test_sha256",
        "demo_sha256", "case_evidence",
    )
    assert {key: filesystem[key] for key in keys} == {key: git[key] for key in keys}
    assert filesystem["case_evidence"]["acceptance"]["zero"]["path"] == "SPEC.md"
    changed = await produce(model, "Fix double. Preserve the return type.", plan, entries)
    assert changed["source_evidence_digest"] != git["source_evidence_digest"]


@pytest.mark.parametrize(("reply", "finish", "reason"), [
    ('{"unsupported":true}', "stop", "acceptance_unsupported_case_contract"),
    ('{"unsupported":true}', "length", "acceptance_response_incomplete"),
    ('{"acceptance":{},"acceptance":{}}', "stop", "acceptance_invalid_json"),
])
async def test_producer_does_not_freeze_unusable_responses(reply, finish, reason):
    entries, plan, _ = inputs()
    model = SimpleNamespace(chat=AsyncMock(
        return_value=SimpleNamespace(text=reply, finish_reason=finish),
    ))
    with pytest.raises(ValueError, match=reason):
        await produce(model, "Fix double.", plan, entries)
