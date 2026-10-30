"""Receipt guards; real Local Runtime native path must be accepted separately."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from Sprout.execution.acceptance import local_executor_binding
from Sprout.execution.local_receipt import check_results, release, settle


def evidence():
    planned = [("acceptance:a", ("python", "a.py")), ("poc", ("python", "demo.py"))]
    output = {"results": [{"kind": kind, "name": f"{kind}: {' '.join(command)}",
                           "outcome": "passed", "passed": True, "started": True,
                           "allowed": True, "exit_code": 0, "error": None,
                           "approval_id": None} for kind, command in planned]}
    return planned, output


@pytest.mark.parametrize("change", ["missing", "duplicate", "failed", "unknown", "pending",
                                    "not_started", "wrong_executor", "forged_verified"])
def test_results_require_actual_complete_checks(change):
    planned, output = evidence()
    binding = local_executor_binding()
    if change == "missing":
        output["results"].pop()
    elif change == "duplicate":
        output["results"][1] = deepcopy(output["results"][0])
    elif change == "failed":
        output["results"][0]["exit_code"] = 1
    elif change == "unknown":
        output["results"][0]["outcome"] = "UNKNOWN"
    elif change == "pending":
        output["pending_approvals"] = ["pending"]
    elif change == "not_started":
        output["results"][0]["started"] = False
    elif change == "wrong_executor":
        binding["fingerprint"] = "0" * 64
    else:
        output = {"verified": True}
    with pytest.raises(PermissionError):
        check_results(planned, output, binding)


def test_valid_checks_are_not_themselves_receipt():
    planned, output = evidence()
    assert check_results(planned, output, local_executor_binding()) == output["results"]


async def test_posix_does_not_issue_receipt(monkeypatch):
    from types import SimpleNamespace

    from Sprout.execution import local_receipt

    monkeypatch.setattr(local_receipt.os, "name", "posix")
    runtime = SimpleNamespace(_local_receipt_candidates={"untrusted": {}},
                              _local_acceptance_receipts={})
    await settle(runtime)
    assert not runtime._local_acceptance_receipts


def test_release_revokes_and_closes_pins():
    from types import SimpleNamespace

    stack = Mock()
    runtime = SimpleNamespace(_local_receipt_candidates={"candidate": {"stack": stack}},
                              _local_acceptance_receipts={}, _local_receipt_plans={"x": []})
    release(runtime)
    assert not runtime._local_receipt_candidates and not runtime._local_receipt_plans
    stack.close.assert_called_once()
