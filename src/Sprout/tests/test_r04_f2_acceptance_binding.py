"""Frozen host data is Task-specific and never an authorization token."""
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from Sprout.gateway.identity import Principal
from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.task.models import DelegationScope, Task


def fixture():
    task = Task(workspace_id="workspace", actor=Principal(
        user_id="host", source="cli", authenticated=True), source="cli",
        delegation_scope=DelegationScope(allowed_paths=("src/app.py", "tests/check.py")),
        metadata={"execution_mode": "code"})
    workspace = SimpleNamespace(id="workspace", root="trusted-root", revision="pinned")
    spec = AcceptanceSpec.from_payload({"cases": [dict(
        id="case", target="src.app:summarize", args=[[]], expected_output={})]})
    return task, workspace, spec


def test_binding_canonical_hash_and_identity():
    from Sprout.execution.host_acceptance import binding_subject, check_binding

    task, workspace, spec = fixture()
    from Sprout.execution.node_result_source import payload_digest

    binding = {"version": 1, "subject": binding_subject(task, workspace),
               "spec": spec.to_payload(), "spec_sha256": payload_digest(spec.to_payload()),
               "test_path": "tests/check.py", "demo_path": "examples/demo.py",
               "resources": {path: {"digest": "a" * 64, "identity": {
                   "device": 1, "inode": 2, "size": 0, "mtime_ns": 3, "ctime_ns": 4}}
                   for path in ("tests/check.py", "examples/demo.py")}}
    binding["sha256"] = payload_digest(binding)
    binding = json.loads(json.dumps(binding))  # Real SQLite metadata round trip.
    assert check_binding(task, workspace, binding).to_payload() == spec.to_payload()
    for changed in (replace(task, id="copied"), replace(task, actor=Principal(user_id="forged"))):
        with pytest.raises(PermissionError):
            check_binding(changed, workspace, binding)
    with pytest.raises(PermissionError):
        check_binding(replace(task, delegation_scope=replace(task.delegation_scope,
            expires_at=datetime.now(UTC) - timedelta(seconds=1))), workspace, binding)
    changed = {**binding, "spec": {"cases": []}}
    with pytest.raises(PermissionError):
        check_binding(task, workspace, changed)


async def test_model_metadata_cannot_install_a_host_binding():
    from Sprout.tests.conftest import build_runtime

    runtime, _ = build_runtime()
    with pytest.raises(PermissionError, match="host_acceptance"):
        await runtime.create_task("missing-workspace", "instruction",
                                  metadata={"host_acceptance_v1": {"trusted": True}})
