"""Explicit single-experiment worker; never replace the shared activity Runtime.

The caller owns a started, isolated Runtime and must commission its dispatcher
separately. This entry neither starts ordinary workers nor grants permissions.
"""

import hashlib
from copy import deepcopy
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from Sprout.evolution.coding_assembly import build_coding_session_factory, coding_task_binding
from Sprout.evolution.replay_plan import verify_plan
from Sprout.execution.coding_commands import PlanBoundCodingCommands
from Sprout.orchestration.commit_workflows import SproutExperimentCommitWorkflow
from Sprout.orchestration.temporal_codec import node_from_dict
from Sprout.orchestration.temporal_workflows import SproutExecutionWorkflow
from Sprout.orchestration.terminal.temporal import TemporalConfig


def experiment_task_queue(task) -> str:
    payload = verify_plan(task.metadata.get("replay_plan"))
    if payload["schema_version"] != 5 or payload["execution_mode"] != "code":
        raise ValueError("V5 isolated coding plan required")
    if not task.id:
        raise ValueError("Explicit experiment task required")
    key = task.id + ":" + task.metadata["replay_plan"]["fingerprint"]
    return "sprout-growth-" + hashlib.sha256(key.encode()).hexdigest()


COMMIT_ROUTING_PROFILE = "isolated-commit-v1"


def commit_task_queue(task, routing_profile):
    base = experiment_task_queue(task)
    if routing_profile is None:
        return base  # Existing immutable requests retain their original queue.
    if routing_profile != COMMIT_ROUTING_PROFILE:
        raise ValueError("Unsupported commit routing profile")
    return base + "-commit-v1"


def _check_experiment_components(runtime, payload):
    directory = Path(payload["repository_root"]).parent.resolve()
    settings = runtime._settings
    if settings.mcp.clients:
        raise ValueError("External MCP clients forbidden in isolated worker")
    for slot in ("operational", "metadata", "session", "context", "usage"):
        expected = f"sqlite:///{(directory / (slot + '.db')).as_posix()}"
        if getattr(settings.storage, slot) != expected:
            raise ValueError("Experiment-owned storage required: " + slot)
    if getattr(runtime, "_isolated_coding_image", None) != payload["coding_profile"]["image"]:
        raise ValueError("Fixed image binding required")


def _check_components(runtime, payload):
    _check_experiment_components(runtime, payload)
    if not callable(getattr(runtime, "_isolated_coding_dispatch", None)):
        raise ValueError("Coding dispatcher required")


class IsolatedCodingWorker:
    """One queue, one Runtime and one task; no growth/publication activities.

    Construction is an explicit host assembly step, not execution authorization.
    Session construction reuses the real plan/config/creator snapshot checks.
    The SDK activity is instance-bound, avoiding set_activity_runtime globals.
    """

    def __init__(self, runtime, task, config: TemporalConfig):
        expected_queue = experiment_task_queue(task)
        if (not config.host or not config.namespace or config.task_queue != expected_queue):
            raise ValueError("Explicit endpoint and experiment-specific queue required")
        payload = verify_plan(task.metadata["replay_plan"])
        if hasattr(runtime, "_isolated_worker_task_binding"):
            raise ValueError("Runtime already belongs to an isolated worker")
        _check_components(runtime, payload)
        # No Runtime mutation until every assembly check has succeeded.
        factory = build_coding_session_factory(runtime, task)
        commands = PlanBoundCodingCommands(runtime, task)
        self._runtime = runtime
        self._task_id = task.id
        self._plan = deepcopy(task.metadata["replay_plan"])
        self._binding = coding_task_binding(task)
        self._config = TemporalConfig(host=config.host, namespace=config.namespace,
                                      task_queue=config.task_queue)
        runtime._isolated_coding_session_factory = factory
        runtime._isolated_coding_commands = commands
        runtime._isolated_worker_task_binding = self._binding

    @activity.defn(name="run_sprout_node")
    async def execute_node(self, data: dict) -> dict:
        if not isinstance(data, dict) or data.get("task_id") != self._task_id:
            raise ApplicationError("Activity belongs to another experiment",
                                   type="IsolatedWorkerScopeMismatch", non_retryable=True)
        try:
            node = await self._admit(data)
        except (ValueError, TypeError, KeyError, OSError):
            # Known admission refusal, no Runtime node execution attempted.
            # Preserve waiting rather than letting SDK retry a configuration
            # error or disclose arbitrary configuration/credential details.
            return {"node_id": data.get("id"), "status": "waiting_resource",
                    "output": {"waiting": True, "waiting_kind": "resource",
                               "waiting_reason": "isolated_worker_binding_changed"}}
        return await self._runtime.execute_node(node)

    async def _admit(self, data):
        task = await self._runtime.storage.metadata.get_task(self._task_id)
        if (task is None or task.metadata.get("replay_plan") != self._plan
                or coding_task_binding(task) != self._binding):
            raise ValueError("Experiment task or plan changed")
        if getattr(self._runtime, "_isolated_worker_task_binding", None) != self._binding:
            raise ValueError("Runtime worker ownership changed")
        _check_components(self._runtime, verify_plan(self._plan))
        # Recheck the live configuration, creator and candidate registration
        # before even READ/PLAN can enter the Runtime. This does not clear an
        # existing node owner or authorize repeating unknown effects.
        build_coding_session_factory(self._runtime, task)
        return node_from_dict(data)

    @activity.defn(name="run_experiment_commit")
    async def execute_commit(self, data: dict) -> dict:
        return await _execute_bound_commit(self, data)

    async def run(self):
        from temporalio.client import Client
        from temporalio.worker import Worker

        client = await Client.connect(self._config.host, namespace=self._config.namespace)
        worker = Worker(client, task_queue=self._config.task_queue,
                        workflows=[SproutExecutionWorkflow, SproutExperimentCommitWorkflow],
                        activities=[self.execute_node, self.execute_commit])
        await worker.run()


async def _execute_bound_commit(bound, data):
    from Sprout.execution.commit_request_execution import execute_commit_request

    if not isinstance(data, dict) or set(data) != {"request_id", "request_fingerprint"}:
        raise ApplicationError("Exact commit request required", non_retryable=True)
    runtime = bound._runtime
    request = await runtime.storage.operational.get_commit_request(data["request_id"])
    dispatch = await runtime.storage.operational.get_commit_dispatch(data["request_id"])
    if (request is None or request["task_id"] != bound._task_id or dispatch is None
            or dispatch["workflow_id"] != activity.info().workflow_id
            or dispatch["request_fingerprint"] != data["request_fingerprint"]
            or (dispatch["host"], dispatch["namespace"], dispatch["task_queue"])
            != (bound._config.host, bound._config.namespace, bound._config.task_queue)):
        raise ApplicationError("Commit belongs to another experiment", non_retryable=True)
    task = await runtime.storage.metadata.get_task(bound._task_id)
    if (task is None or coding_task_binding(task) != bound._binding
            or getattr(runtime, "_isolated_worker_task_binding", None) != bound._binding
            or commit_task_queue(task, request["review"].get("routing_profile"))
            != bound._config.task_queue):
        raise ApplicationError("Worker or routing binding changed", non_retryable=True)
    if getattr(runtime, "_isolated_worker_role", None) == "commit_only":
        _check_experiment_components(runtime, verify_plan(bound._plan))
    else:
        _check_components(runtime, verify_plan(bound._plan))
    build_coding_session_factory(runtime, task)
    return await execute_commit_request(runtime, data["request_id"], data["request_fingerprint"])


class IsolatedCommitWorker:
    """Independent Runtime, commit-only queue and activities; no generic startup.

    Caller owns storage lifetime. Construction never installs coding commands,
    executes a model, starts cleanup/outbox loops or clears unknown ownership.
    """

    def __init__(self, runtime, task, config):
        if (not config.host or not config.namespace
                or config.task_queue != commit_task_queue(task, COMMIT_ROUTING_PROFILE)):
            raise ValueError("Dedicated commit queue required")
        if hasattr(runtime, "_isolated_worker_task_binding"):
            raise ValueError("Commit worker requires its own Runtime")
        payload = verify_plan(task.metadata["replay_plan"])
        _check_experiment_components(runtime, payload)
        build_coding_session_factory(runtime, task)  # Validate; do not install execution callbacks.
        self._runtime, self._task_id = runtime, task.id
        self._plan = deepcopy(task.metadata["replay_plan"])
        self._binding = coding_task_binding(task)
        self._config = TemporalConfig(host=config.host, namespace=config.namespace,
                                      task_queue=config.task_queue)
        runtime._isolated_worker_task_binding = self._binding
        runtime._isolated_worker_role = "commit_only"

    @activity.defn(name="run_experiment_commit")
    async def execute_commit(self, data: dict) -> dict:
        return await _execute_bound_commit(self, data)

    async def run(self):
        from temporalio.client import Client
        from temporalio.worker import Worker

        client = await Client.connect(self._config.host, namespace=self._config.namespace)
        worker = Worker(client, task_queue=self._config.task_queue,
                        workflows=[SproutExperimentCommitWorkflow],
                        activities=[self.execute_commit])
        await worker.run()
