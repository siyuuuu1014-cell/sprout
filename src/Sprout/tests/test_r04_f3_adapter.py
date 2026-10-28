"""P1.5 protocol and lifecycle; no container or target code execution."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Sprout.execution.models import ProcessResult
from Sprout.execution.verification_manifest import _VerificationExecution
from Sprout.execution.verification_run import DockerVerificationRun
from Sprout.tests.test_r04_f3_manifest import execution as execution
from Sprout.tests.test_r04_f3_manifest import f3 as f3
from Sprout.tests.test_r04_f3_manifest import rig as rig


def test_compile_copy_is_exact_and_does_not_write_host_inputs(tmp_path):
    import hashlib

    from Sprout.execution.verification_observer import materialize_inputs

    source, destination = tmp_path / "inputs", tmp_path / "disposable"
    source.mkdir()
    destination.mkdir()
    content = b"value = 1\n"
    (source / "app.py").write_bytes(content)
    (source / "unselected.py").write_bytes(b"not selected")
    materialize_inputs({"app.py": hashlib.sha256(content).hexdigest()}, source, destination)
    assert (destination / "app.py").read_bytes() == content
    assert not (destination / "unselected.py").exists()
    assert (source / "app.py").read_bytes() == content
    with pytest.raises(FileExistsError):
        materialize_inputs({"app.py": hashlib.sha256(content).hexdigest()}, source, destination)


@pytest.mark.parametrize("name,content,digest", [
    ("../escape.py", b"x", "0" * 64),
    (".env", b"x", "0" * 64),
    ("app.py", b"x", "0" * 64),
    ("app.py", b"x" * 100001, "0" * 64),
], ids=["escape", "private", "digest", "budget"])
def test_compile_copy_rejects_escape_digest_and_budget(tmp_path, name, content, digest):
    from Sprout.execution.verification_observer import materialize_inputs

    source, destination = tmp_path / "inputs", tmp_path / "disposable"
    source.mkdir()
    destination.mkdir()
    (source / "app.py").write_bytes(content)
    with pytest.raises(ValueError):
        materialize_inputs({name: digest}, source, destination)
    assert not tuple(destination.iterdir())


def test_structured_observer_rejects_plain_stdout():
    from Sprout.execution.verification_run import DockerVerificationRun

    with pytest.raises(ValueError):
        DockerVerificationRun.command_observation({}, "9 passed", 0, {})


def test_owned_lifecycle_is_not_legacy_pytest_execution():
    from Sprout.execution.verification_run import DockerVerificationRun

    assert callable(getattr(DockerVerificationRun, "execute_prepared", None))


def plan():
    context = {
        "task": "task",
        "node": "node",
        "owner": "owner",
        "manifest": "d" * 64,
        "image": "sha256:" + "a" * 64,
        "obligation": "demo",
    }
    return {
        "task_id": "task",
        "node_id": "node",
        "owner": "owner",
        "manifest": "d" * 64,
        "container_name": "sprout-verification-unit",
        "image": "sha256:" + "a" * 64,
        "nonce": "nonce-unit",
        "observer_hash": "e" * 64,
        "request": {
            "context": context,
            "nonce": "nonce-unit",
            "argv": ["python", "examples/demo.py"],
            "cwd": "/workspace",
            "tools": {"python": {"path": "/usr/bin/python3", "sha256": "f" * 64}},
        },
        "command": ("docker", "run", "--mount", "type=bind,src=/unit,dst=/workspace,readonly"),
    }


def inspection(value, *, status="exited", code=0):
    result = {
        "Id": "1" * 64,
        "Name": "/" + value["container_name"],
        "Image": value["image"],
        "Config": {
            "User": "65532:65532",
            "Labels": {
                "sprout.role": "independent-verification",
                **{
                    "sprout.verification." + k: value[field]
                    for k, field in (
                        ("task", "task_id"),
                        ("node", "node_id"),
                        ("owner", "owner"),
                        ("manifest", "manifest"),
                    )
                },
            },
        },
        "State": {
            "Status": status,
            "Running": status == "running",
            "Pid": 12 if status == "running" else 0,
            "ExitCode": code,
        },
        "Mounts": [{"Type": "bind", "Source": "/unit", "Destination": "/workspace", "RW": False}],
        "HostConfig": {
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "Privileged": False,
            "PidsLimit": 64,
            "Memory": 512 * 1024 * 1024,
            "MemorySwap": 512 * 1024 * 1024,
            "NanoCpus": 1_000_000_000,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
        },
    }
    if value.get("protocol") == "observed-command-v1":
        command = value["command"]
        result["HostConfig"]["Tmpfs"] = dict(
            x.split("=", 1)[1].split(":", 1) for x in command if x.startswith("--tmpfs="))
        result["Config"].update(
            Entrypoint=[next(x.split("=", 1)[1] for x in command if x.startswith("--entrypoint="))],
            WorkingDir="/workspace",
            Cmd=list(command[command.index(value["image"]) + 1 :]),
        )
    return result


def observation(value, *, code=0):
    payload = {
        "schema_version": 1,
        **deepcopy(value["request"]),
        "observer_sha": value["observer_hash"],
        "exit_code": code,
        "duration_ms": 2.0,
        "status": "completed",
        "stdout": "demo result",
        "stderr": "",
    }
    return "SPROUT_COMMAND_REPORT " + value["nonce"] + " " + json.dumps(payload)


@pytest.mark.parametrize(
    "change",
    [
        None,
        "nonce",
        "owner",
        "task",
        "image",
        "observer",
        "argv",
        "tool",
        "exit",
        "running",
        "mount",
        "network",
    ],
)
def test_protocol_and_daemon_identity_are_both_required(change):
    value = plan()
    payload = inspection(value)
    stdout = observation(value)
    if change == "nonce":
        stdout = stdout.replace("nonce-unit", "wrong")
    if change in {"owner", "task", "image", "argv", "tool"}:
        value = deepcopy(value)
        if change in {"owner", "task", "image"}:
            value["request"]["context"][change] = "wrong"
        elif change == "argv":
            value["request"]["argv"] = ["sh", "-c", "anything"]
        else:
            value["request"]["tools"] = {}
    if change == "observer":
        value["observer_hash"] = "wrong"
    if change == "exit":
        payload["State"]["ExitCode"] = 1
    if change == "running":
        payload["State"].update(Running=True, Pid=4, Status="running")
    if change in {"mount", "network"}:
        if change == "mount":
            payload["Mounts"][0]["RW"] = True
        else:
            payload["HostConfig"]["NetworkMode"] = "host"
        with pytest.raises(ValueError):
            DockerVerificationRun.inspect_limits(value, payload)
    elif change:
        with pytest.raises(ValueError):
            DockerVerificationRun.command_observation(value, stdout, 0, payload)
    else:
        DockerVerificationRun.inspect_limits(value, payload)
        result = DockerVerificationRun.command_observation(value, stdout, 0, payload)
        assert result["executed"] and result["passed"] and result["exit_code"] == 0


@pytest.mark.parametrize(
    "mode", ["success", "failed", "timeout", "cancel", "daemon", "spoof", "repeat"]
)
async def test_owned_lifecycle_uses_daemon_stop_evidence(mode):
    value = plan()
    calls = []
    native = {}
    state = ["created"]
    metadata = SimpleNamespace(
        claim_verification_execution=AsyncMock(return_value=True),
        finish_verification_execution=AsyncMock(return_value=True),
    )
    fresh = AsyncMock()
    scope = SimpleNamespace(
        task=SimpleNamespace(id="task"),
        node=SimpleNamespace(id="node"),
        runtime=SimpleNamespace(_verification_preparation_record=fresh),
    )
    broker = SimpleNamespace()
    binding = _VerificationExecution(
        broker, scope, object(), {}, "demo", "approval", asyncio.current_task()
    )
    broker._verification_execution_record = AsyncMock(
        return_value=(native, value, {"schema_version": 3, "task_id": "task", "owner": "owner"})
    )

    async def step(key, action, container_id):
        assert key is binding
        calls.append(action)
        if action == "create":
            return ProcessResult(("docker",), 0, "1" * 64, started=True)
        if action == "start":
            state[0] = "running"
            if mode == "timeout":
                raise TimeoutError("controlled CLI timeout")
            if mode == "cancel":
                raise asyncio.CancelledError
            if mode == "daemon":
                raise OSError("controlled daemon lost")
            state[0] = "exited"
            code = 1 if mode == "failed" else 0
            return ProcessResult(
                ("docker",),
                code,
                "fake success" if mode == "spoof" else observation(value, code=code),
                started=True,
            )
        if action in {"stop", "kill"}:
            state[0] = "exited"
            return ProcessResult(("docker",), 0, started=True)
        if action == "inspect":
            if mode == "daemon" and "start" in calls:
                raise OSError("controlled daemon lost")
            return ProcessResult(
                ("docker",),
                0,
                json.dumps([inspection(value, status=state[0], code=1 if mode == "failed" else 0)]),
                started=True,
            )
        raise AssertionError(action)

    broker._verification_step = step
    broker._verification_cleanup_task = lambda key, task: native.update(cleanup_task=task)
    runner = DockerVerificationRun(metadata, None)
    if mode == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await runner.execute_prepared(binding)
        result = metadata.finish_verification_execution.call_args.args[-1]
        assert result["cancelled"] and result["stopped_observed"]
    else:
        result = await runner.execute_prepared(binding)
        if mode == "success":
            assert result["passed"] and result["executed"]
        elif mode == "failed":
            assert result["outcome"] == "check_failed"
        elif mode in {"timeout", "daemon", "spoof"}:
            assert result["outcome"] == "UNKNOWN" and not result["passed"]
        if mode == "repeat":
            metadata.claim_verification_execution.return_value = False
            before = list(calls)
            result = await runner.execute_prepared(binding)
            assert calls == before and result["outcome"] == "UNKNOWN"
    assert calls.count("create") == 1 and calls.count("start") == 1
    if mode in {"cancel", "timeout"}:
        assert "stop" in calls
    assert not result.get("retry_allowed", False)


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "cancel-task",
        "scope-expired",
        "policy-revoked",
        "wrong-container",
        "daemon-switch",
        "stop-fails",
        "cancel-coroutine",
        "pending",
        "daemon-before",
        "owner-record-changed",
    ],
)
async def test_real_runtime_guard_broker_claim_and_report_with_substituted_docker(
    f3, monkeypatch, mode
):
    """Real Runtime/SQLite/Approval, substituted processes and files, not native proof."""
    import base64
    import hashlib

    e = f3
    runtime = e.rig.runtime
    host = runtime._verification_hosts[e.rig.task.id]
    executable = e.root.parent / "controlled-docker-cli"
    executable.write_bytes(b"unit executor identity - never executed")
    host["docker"] = {
        "path": str(executable),
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "context": "unit-local",
        "endpoint": "unix:///unit-docker.sock",
        "daemon_id": "unit-daemon",
    }
    host["discovery"] = {
        "confirmed": True,
        "test_files": ["tests/frozen.py"],
        "config_files": [],
        "plugins": [],
        "plugin_autoload": False,
    }
    host["tools"] = {
        name: {"path": "/usr/bin/" + name, "sha256": "f" * 64}
        for name in ("python", "pytest", "ruff")
    }
    host["tools"]["ruff"].update(configuration_confirmed=True, config_files=[])
    host["tools"]["pytest"]["plugins"] = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-not-a-real-key")
    approvals = []
    waiter = e.broker.await_verification_approval

    async def human(manifest, issuer, scope, exported, approval_id, **kwargs):
        approvals.append(approval_id)
        await e.broker._approvals.decide(approval_id, True, decided_by="controlled-unit-human")
        return await waiter(manifest, issuer, scope, exported, approval_id, **kwargs)

    monkeypatch.setattr(e.broker, "await_verification_approval", human)
    if mode == "pending":
        execute = e.broker.execute_verification_obligation

        async def pending(manifest, issuer, scope, exported, obligation_id, **kwargs):
            return await execute(manifest, issuer, scope, exported, obligation_id, wait=False)

        monkeypatch.setattr(e.broker, "execute_verification_obligation", pending)
    containers = {}
    starts = []

    async def docker(*argv, **kwargs):
        assert "DEEPSEEK_API_KEY" not in kwargs["env"]
        assert kwargs["cwd"] == str(host["parent"])
        assert argv[1] == "--context=unit-local"
        action = argv[2]
        if action in {"context", "info"}:
            output = json.dumps(
                host["docker"]["endpoint"]
                if action == "context"
                else "wrong-daemon"
                if (mode == "daemon-switch" and starts) or mode == "daemon-before"
                else "unit-daemon"
            )
        elif action == "create":
            payload = json.loads(base64.b64decode(argv[-1]))
            cid = hashlib.sha256(payload["nonce"].encode()).hexdigest()
            prepared = next(
                p
                for record in runtime._verification_preparations.values()
                for p in record["plans"].values()
                if p.get("nonce") == payload["nonce"]
            )
            containers[cid] = [prepared, "created"]
            output = cid
        elif action == "start":
            cid = argv[-1]
            prepared = containers[cid][0]
            from dataclasses import replace

            active = next(
                record["native"]["binding"]
                for record in runtime._verification_preparations.values()
                if record.get("native", {}).get("container_id") == cid
            )
            with pytest.raises(PermissionError):
                await e.broker._verification_step(replace(active), "stop", cid)
            with pytest.raises(PermissionError):
                await e.broker._verification_step(active, "stop", "2" * 64)
            starts.append(prepared["request"]["argv"])
            if mode != "success":
                from dataclasses import replace
                from datetime import UTC, datetime, timedelta

                from Sprout.security.access import AccessDecision
                from Sprout.task.models import TaskStatus

                containers[cid][1] = "running"
                if mode == "cancel-coroutine":
                    raise asyncio.CancelledError
                if mode == "owner-record-changed":
                    read = e.rig.storage.metadata.get_verification_execution

                    async def wrong_owner(key):
                        row = await read(key)
                        return {**row, "owner": "wrong"} if row else None

                    monkeypatch.setattr(
                        e.rig.storage.metadata, "get_verification_execution", wrong_owner
                    )
                if mode == "cancel-task":
                    await e.rig.storage.metadata.update_task_status(
                        e.rig.task.id, TaskStatus.CANCELLED
                    )
                    runtime._verification_hosts.pop(e.rig.task.id)
                if mode == "scope-expired":
                    await e.rig.storage.metadata.save_task(
                        replace(
                            e.rig.task,
                            delegation_scope=replace(
                                e.rig.task.delegation_scope,
                                expires_at=datetime.now(UTC) - timedelta(seconds=1),
                            ),
                        )
                    )
                if mode == "policy-revoked":
                    e.mode[0] = AccessDecision.DENY
                if mode in {"cancel-task", "scope-expired", "policy-revoked"}:
                    with pytest.raises(PermissionError):
                        await e.broker._verification_step(active, "start", cid)
                raise TimeoutError("controlled post-start interruption")
            containers[cid][1] = "exited"
            request = prepared["request"]
            result = {
                "schema_version": 1,
                **deepcopy(request),
                "observer_sha": prepared["observer_hash"],
                "exit_code": 0,
                "duration_ms": 1.0,
                "status": "completed",
                "stdout": "demo",
                "stderr": "",
            }
            if "pytest" in request["argv"]:
                probe = {
                    "nonce": prepared["nonce"],
                    "exit_code": 0,
                    "collected": ["tests/frozen.py::test_normal"],
                    "collection_errors": [],
                    "configuration": {"plugins": [], "relative_config": None, "config_hash": None},
                    "runner": {"probe_hash": request["pytest_observer_sha"]},
                    "reports": [
                        {
                            "nodeid": "tests/frozen.py::test_normal",
                            "phase": "call",
                            "outcome": "passed",
                        }
                    ],
                }
                result["stdout"] = (
                    "SPROUT_PYTEST_REPORT " + prepared["nonce"] + " " + json.dumps(probe)
                )
            if request["argv"][1:3] == ["-m", "compileall"]:
                assert request["materialize"] == prepared["input_manifest"]
                assert all(a.endswith(",readonly") for a in prepared["command"]
                           if a.startswith("type=bind"))
                assert any(a.startswith("--tmpfs=/workspace:") for a in prepared["command"])
            output = "SPROUT_COMMAND_REPORT " + prepared["nonce"] + " " + json.dumps(result)
        elif action == "inspect":
            cid = argv[-1]
            prepared, state = containers[cid]
            payload = inspection(prepared, status=state)
            payload["Id"] = cid
            if mode == "wrong-container" and starts:
                payload["Id"] = "9" * 64
            payload["Mounts"] = []
            for i, argument in enumerate(prepared["command"]):
                if argument == "--mount":
                    pieces = dict(
                        x.split("=", 1) if "=" in x else (x, True)
                        for x in prepared["command"][i + 1].split(",")
                    )
                    payload["Mounts"].append(
                        {
                            "Type": "bind",
                            "Source": pieces["src"],
                            "Destination": pieces["dst"],
                            "RW": not pieces.get("readonly", False),
                        }
                    )
            output = json.dumps([payload])
        elif action in {"stop", "kill"}:
            if mode == "stop-fails" and action == "stop":
                raise OSError("controlled stop failed; inspect still available")
            containers[argv[-1]][1] = "exited"
            output = ""
        else:
            raise AssertionError(action)
        stdout = asyncio.StreamReader()
        stdout.feed_data(output.encode())
        stdout.feed_eof()
        stderr = asyncio.StreamReader()
        stderr.feed_eof()
        return SimpleNamespace(
            returncode=0, stdout=stdout, stderr=stderr, wait=AsyncMock(return_value=0)
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", docker)
    with runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        if mode == "cancel-coroutine":
            with pytest.raises(asyncio.CancelledError):
                await runtime._evaluate_host_verification(e.rig.node)
            assert len(starts) == 1 and all(state == "exited" for _, state in containers.values())
            return
        if mode in {"cancel-task", "scope-expired"}:
            with pytest.raises(PermissionError):
                await runtime._evaluate_host_verification(e.rig.node)
            assert len(starts) == 1 and all(state == "exited" for _, state in containers.values())
            return
        result = await runtime._evaluate_host_verification(e.rig.node)
    if mode != "success":
        assert not result["verified"]
        if mode in {"pending", "daemon-before"}:
            assert not starts and not containers
            from Sprout.security.approval import ApprovalStatus

            ids = (
                approvals
                if mode == "daemon-before"
                else [row["execution"]["approval_id"] for row in result["results"]]
            )
            for approval_id in ids:
                status = (await e.broker._approvals.store.get_approval(approval_id)).status
                assert status is (
                    ApprovalStatus.PENDING if mode == "pending" else ApprovalStatus.APPROVED
                )
        elif mode in {"wrong-container", "daemon-switch", "owner-record-changed"}:
            assert any(state == "running" for _, state in containers.values())
        else:
            assert all(state == "exited" for _, state in containers.values())
        return
    # The substituted process lifecycle completed. A same-process pytest probe
    # is still not an independent test verdict, even with a normal exit code.
    assert not result["verified"] and not result["verification_report"]["verified"]
    assert len(starts) == 6 and len(approvals) == 6
    assert all(r["executed"] and r["exit_code"] == 0 for r in result["results"])
    assert starts.count(["pytest"]) == 1 and starts.count(["pytest", "-q"]) == 1
    assert all(state == "exited" for _, state in containers.values())
    for row in result["results"]:
        pytest_result = row["kind"] in {"project-pytest", "strategy-pytest"} or row[
            "kind"].startswith("acceptance:")
        assert row["outcome"] == ("unverified" if pytest_result else "passed")
        assert row["passed"] is (not pytest_result)
        saved = await runtime.storage.metadata.get_verification_execution(
            row["execution"]["execution_id"])
        assert saved["state"] == "completed" and saved["schema_version"] == 3
        assert saved["task_id"] == e.rig.task.id
        assert saved["node_id"] == result["manifest"]["node"]
        assert saved["owner"] == result["manifest"]["owner"]
    from Sprout.security.approval import ApprovalStatus

    for approval_id in approvals:
        assert (await e.broker._approvals.store.get_approval(approval_id)).status is (
            ApprovalStatus.CONSUMED)

    from Sprout.execution.models import ChangeProposal, ChangeProposalStatus
    from Sprout.execution.models import TestResult as Check
    from Sprout.execution.verification import proposal_verification_report
    from Sprout.runtime.changes import ChangeProposalService
    from Sprout.storage.local.memory import MemoryOperationalStore

    proposal = ChangeProposal(task_id=e.rig.task.id, status=ChangeProposalStatus.APPROVED,
        test_results=tuple(Check(**item) for item in result["test_results"]),
        metadata={"verification": result})
    report = proposal_verification_report(proposal)
    assert not report.verified and "unverified" in report.failure_codes
    metadata = SimpleNamespace(get_change_proposal=AsyncMock(return_value=proposal),
        get_task=AsyncMock(return_value=e.rig.task), get_workspace=AsyncMock())
    apply_broker = SimpleNamespace(apply=AsyncMock())
    denied = await ChangeProposalService(metadata, MemoryOperationalStore(), apply_broker).apply(
        proposal.id)
    assert not denied.applied
    apply_broker.apply.assert_not_called()
    metadata.get_workspace.assert_not_called()


async def test_cached_disposable_copy_does_not_reintroduce_its_own_blocker(f3, monkeypatch):
    e = f3
    prepare = e.broker.prepare_verification

    async def twice(manifest, issuer, scope, exported):
        first = await prepare(manifest, issuer, scope, exported)
        second = await prepare(manifest, issuer, scope, exported)
        assert second["compileall"]["reason"] == first["compileall"]["reason"]
        assert second["compileall"]["reason"] != "verification_disposable_copy_required"
        return second

    monkeypatch.setattr(e.broker, "prepare_verification", twice)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        result = await e.rig.runtime._evaluate_host_verification(e.rig.node)
    assert not result["verified"]
    e.spawn.assert_not_called()


async def test_process_denied_on_imported_input_never_asks_approval(f3, monkeypatch):
    from Sprout.security.access import AccessDecision, ActionType, PolicyDecision

    e = f3
    decide = e.broker._policy.decide

    def deny_import(request):
        if request.action is ActionType.PROCESS_RUN and request.resource.path.endswith(
            "/src/app.py"
        ):
            return PolicyDecision(AccessDecision.DENY, "imported executable denied")
        return decide(request)

    monkeypatch.setattr(e.broker._policy, "decide", deny_import)
    approval = AsyncMock(side_effect=AssertionError("Unexpected approval"))
    monkeypatch.setattr(e.broker._approvals, "request", approval)
    with e.rig.runtime._project_analysis_scope(e.rig.task, e.rig.workspace, e.rig.node):
        result = await e.rig.runtime._evaluate_host_verification(e.rig.node)
    assert all(r["outcome"] == "policy_denied" for r in result["results"])
    approval.assert_not_called()
    e.spawn.assert_not_called()


async def test_runtime_stop_keeps_custody_until_owner_cancel_handler_finishes(f3):
    from Sprout.execution.verification_manifest import VerificationManifestV1

    runtime = f3.rig.runtime
    key = VerificationManifestV1(
        "{}"
    )  # Data for a controlled lifecycle worker, not an execution grant.
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def owner():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert key in runtime._verification_preparations
            assert await runtime.storage.metadata.get_task(f3.rig.task.id) is not None
            cleaned.set()
            raise

    task = asyncio.create_task(owner())
    await started.wait()
    runtime._verification_preparations[key] = {"owner": task}
    await runtime.stop()
    assert cleaned.is_set() and task.done()
    assert not runtime._verification_preparations
