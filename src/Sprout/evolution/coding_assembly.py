"""Plan-bound production session construction; never silently enable task execution."""

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from Sprout.evolution.regression_spec import frozen
from Sprout.evolution.replay_config import configuration_blockers
from Sprout.evolution.replay_plan import verify_plan
from Sprout.evolution.replay_snapshot import snapshot_blockers
from Sprout.execution.coding_session import CodingNodeSession
from Sprout.execution.git_transaction_contract import fingerprint
from Sprout.execution.isolated_git_plan import IsolatedGitPlan
from Sprout.gateway.local_identity import creator_blockers, current_cli_principal
from Sprout.orchestration.models import NodeStatus
from Sprout.task.models import TaskStatus


def coding_task_binding(task):
    """Stable execution inputs shared by session and dedicated worker admission."""
    scope = {key: sorted(value) if isinstance(value, (set, frozenset)) else value
             for key, value in asdict(task.delegation_scope).items()}
    return fingerprint({"id": task.id, "workspace_id": task.workspace_id,
        "instruction": task.instruction, "budget": asdict(task.budget),
        "scope": scope, "actor": asdict(task.actor), "source": task.source,
        "inputs": {key: task.metadata.get(key) for key in (
            "execution_mode", "replay_plan", "replay_source_task_id", "acceptance_spec",
            "regression_fingerprint", "require_acceptance_tests", "required_paths", "steps")}})


def _check_runtime(runtime, task, payload):
    if getattr(runtime, "_isolated_coding_required", False) is not True:
        raise ValueError("Explicit isolated coding runtime required")
    settings = getattr(runtime, "_settings", None)
    if settings is None:
        raise ValueError("Actual runtime configuration required")
    principal = current_cli_principal()
    blockers = creator_blockers(payload.get("creator"), principal)
    blockers.extend(configuration_blockers(payload.get("configuration"), settings))
    blockers.extend(snapshot_blockers(payload.get("runtime_snapshot"), runtime))
    if blockers:
        raise ValueError("Coding assembly blocked: " + ", ".join(blockers))
    if (task.actor != principal or task.source != "cli" or task.execution_mode != "code"
            or task.metadata.get("replay_source_task_id") != payload["source_task_id"]):
        raise ValueError("Persisted CLI task identity or mode mismatch")
    for name in ("run_task_process", "authorize_task_process"):
        if not callable(getattr(runtime, name, None)):
            raise ValueError("Authorized runtime process interface required")


def build_coding_session_factory(runtime, task):
    """Construct a host-owned factory without installing dispatch or starting Runtime.

    The caller must supply the persisted task, not a user-imported plan. Every
    prepare subsequently rechecks that binding against the metadata authority.
    Image commissioning and model-tool protection remain separate prerequisites.
    """
    pinned = deepcopy(task)
    payload = verify_plan(pinned.metadata.get("replay_plan"))
    if payload["schema_version"] != 5 or payload["execution_mode"] != "code":
        raise ValueError("V5 creator-bound coding plan required")
    _check_runtime(runtime, pinned, payload)
    binding = coding_task_binding(pinned)
    repo, original = Path(payload["repository_root"]), Path(payload["original_root"])
    directory = repo.parent
    # Validate the frozen regression before returning any usable callback.
    regression = frozen(directory, pinned.metadata.get("regression_fingerprint"))
    broker = getattr(runtime, "_file_broker", None)
    if broker is None or any(not callable(getattr(broker, name, None)) for name in
                             ("write_text", "delete", "authorize_delete")):
        raise ValueError("Guarded file broker required")

    class BoundSession(CodingNodeSession):
        async def prepare(self):
            current = await self.metadata.get_task(pinned.id)
            persisted_node = await self.metadata.get_execution_node(self.node.id)
            workspace = await self.metadata.get_workspace(pinned.workspace_id)
            if (current is None or current.status != TaskStatus.RUNNING
                    or coding_task_binding(current) != binding or workspace is None
                    or workspace.root.resolve() != repo.resolve()):
                raise ValueError("Coding task input or workspace binding changed")
            guard = self.node.metadata.get("execution_guard", {})
            if (persisted_node is None or persisted_node.task_id != pinned.id
                    or persisted_node.status != NodeStatus.RUNNING
                    or persisted_node.metadata.get("execution_guard", {}).get("state") != "active"
                    or persisted_node.metadata["execution_guard"].get("owner")
                    != guard.get("owner")):
                raise ValueError("Live coding node ownership changed")
            _check_runtime(runtime, current, payload)
            if frozen(directory, current.metadata.get("regression_fingerprint")) != regression:
                raise ValueError("Frozen coding regression changed")
            planner = IsolatedGitPlan(workspace, original=original, evidence=directory)
            planner._check_git_root(self.sandbox.root)
            evidence = directory / "coding-evidence"
            if evidence.is_symlink():
                raise ValueError("Linked coding evidence directory forbidden")
            evidence.mkdir(exist_ok=True)
            return await super().prepare()

    def factory(*, task, node, sandbox):
        if (coding_task_binding(task) != binding or node.task_id != pinned.id
                or node.status != NodeStatus.RUNNING
                or node.metadata.get("execution_guard", {}).get("state") != "active"
                or not node.metadata["execution_guard"].get("owner")):
            raise ValueError("Live plan-bound coding callback required")
        return BoundSession(runtime.storage.metadata, runtime, broker,
            evidence=directory / "coding-evidence", original=original,
            image=payload["coding_profile"]["image"], oracle_paths=tuple(payload["oracle_paths"]),
            sandbox=deepcopy(sandbox), task=deepcopy(task), node=deepcopy(node),
            regression_spec=regression)

    return factory
