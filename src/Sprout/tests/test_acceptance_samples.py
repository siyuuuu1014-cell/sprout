from types import SimpleNamespace

import pytest

from Sprout.execution.isolated_auxiliary import auxiliary_commands
from Sprout.orchestration.compiler import ExecutionGraphBuilder
from Sprout.orchestration.models import NodeType
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import Task
from Sprout.tests.acceptance_samples import check_sample_task, sample_definition
from Sprout.tests.cart_acceptance_fixture import PROPERTY_PROBE
from Sprout.workspace.models import ReadPlan, WorkspaceManifest


@pytest.mark.parametrize("name", ["addition", "cart"])
def test_each_sample_has_valid_oracles_and_own_task_binding(name):
    sample = sample_definition(name)
    assert AcceptanceSpec.from_payload(sample["acceptance"]).cases
    assert AcceptanceSpec.from_payload(sample["regression"]).cases
    task = SimpleNamespace(instruction=sample["instruction"], metadata={
        "required_paths": sample["required_paths"],
        "verification_commands": sample["verification_commands"]})
    assert check_sample_task(name, task) == sample
    task.metadata["required_paths"] = ["unrelated.py"]
    with pytest.raises(ValueError, match="persisted task"):
        check_sample_task(name, task)


def test_cart_property_probe_never_enters_public_source_files():
    sample = sample_definition("cart")
    assert PROPERTY_PROBE not in "\n".join(sample["sources"].values())
    assert sample["verification_commands"]
    assert "tests/test_generated.py" in sample["required_paths"]


def test_unknown_sample_is_not_silently_addition():
    with pytest.raises(ValueError):
        sample_definition("other")


def test_legacy_addition_task_without_extra_checks_still_matches():
    sample = sample_definition("addition")
    task = SimpleNamespace(instruction=sample["instruction"],
        metadata={"required_paths": sample["required_paths"]})
    check_sample_task("addition", task)


def test_property_check_survives_graph_compilation_and_auxiliary_normalization():
    sample = sample_definition("cart")
    task = Task(instruction=sample["instruction"], metadata={"execution_mode": "code"})
    graph = ExecutionGraphBuilder().build(task, ReadPlan(task_id=task.id),
        WorkspaceManifest(workspace_id="trial", test_commands=("pytest",)),
        verification_commands=tuple(sample["verification_commands"]))
    evaluations = [node for node in graph.nodes if node.type is NodeType.EVALUATION]
    assert evaluations
    for node in evaluations:
        commands = auxiliary_commands(node.metadata)
        properties = [command for kind, command in commands if kind == "cart-properties"]
        assert properties == [("python", "-I", "-c", PROPERTY_PROBE)]
    assert all(PROPERTY_PROBE not in str(node.metadata) for node in graph.nodes
               if node.type in {NodeType.PLAN, NodeType.AGENT})


def test_commit_binding_rejects_removed_property_check():
    sample = sample_definition("cart")
    task = SimpleNamespace(instruction=sample["instruction"], metadata={
        "required_paths": sample["required_paths"], "verification_commands": []})
    with pytest.raises(ValueError, match="verification commands"):
        check_sample_task("cart", task)
