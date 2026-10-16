from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.coding_container import CodingCommand
from Sprout.execution.isolated_auxiliary import auxiliary_commands, run_auxiliary_checks
from Sprout.execution.models import ProcessResult


@pytest.mark.parametrize("payload", [
    {"test_commands": "pytest"}, {"build_commands": [""]},
    {"verification_commands": ["pytest"]},
    {"verification_commands": [{"kind": "schema"}]},
])
def test_malformed_required_checks_rejected(payload):
    with pytest.raises(ValueError):
        auxiliary_commands(payload)


def test_commands_preserve_quoted_arguments_and_criterion_kind():
    assert auxiliary_commands({"test_commands": ['python "a file.py"'],
                               "verification_commands": [{"kind": "schema",
                                                           "command": ["python", "db.py"]}]}) == [
        ("test", ("python", "a file.py")), ("schema", ("python", "db.py"))]


@pytest.mark.parametrize("mode", ["passed", "failed", "approval", "unknown",
                                  "skipped", "missing_tests"])
async def test_independent_exports_never_change_shared_input(tmp_path, monkeypatch, mode):
    original, evidence = tmp_path / "original", tmp_path / "evidence"
    original.mkdir()
    evidence.mkdir()
    exported = evidence / "coding-input"
    exported.mkdir()
    (exported / "app.py").write_text("value = 1")
    context = CodingCommand("task", original, evidence, exported, "sha256:" + "a" * 64,
                            ("python",), ("oracle.py",))
    node = SimpleNamespace(id="node", task_id="task", metadata={
        "execution_guard": {"owner": "owner", "state": "active"}})
    requests = []

    class Backend:
        def __init__(self, *args, **kwargs):
            pass

        async def execute(self, request, **kwargs):
            requests.append(request)
            assert (request.exported / "app.py").read_text() == "value = 1"
            (request.exported / "app.py").write_text("build artifact")
            if mode == "unknown":
                raise RuntimeError("unconfirmed")
            return ProcessResult(request.command, allowed=mode != "approval",
                                 exit_code=(5 if mode == "missing_tests"
                                            else 1 if mode == "failed" else 0),
                                 stdout="1 passed, 1 skipped" if mode == "skipped" else "1 passed",
                                 approval_id="grant" if mode == "approval" else None)

    monkeypatch.setattr("Sprout.execution.isolated_auxiliary.DockerCodingBackend", Backend)
    metadata = SimpleNamespace(ensure_coding_intent=AsyncMock())
    call = run_auxiliary_checks(metadata, None, context=context, node=node,
                                commands=[("test", ("python", "-m", "pytest")),
                                          ("build", ("python", "app.py"))])
    if mode == "unknown":
        with pytest.raises(RuntimeError, match="unconfirmed"):
            await call
    else:
        result = await call
        assert result["waiting"] is (mode == "approval")
        if mode != "approval":
            assert result["verified"] is (mode == "passed")
            assert len(requests) == 2
            assert requests[0].exported != requests[1].exported
    assert (exported / "app.py").read_text() == "value = 1"
    assert all(not request.read_only_input for request in requests)
    metadata.ensure_coding_intent.assert_awaited_once()
