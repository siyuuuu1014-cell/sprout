"""Native regression for CLI-style file scopes; no model or historical data."""

import asyncio
import json
import os
import tempfile
from dataclasses import replace

import pytest

from Sprout.tests.r04_g1_native_cases import git
from Sprout.tests.test_r04_e_apply_native import prepare_apply_fixture

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SPROUT_NATIVE_LOCAL_RECEIPT") != "1",
    reason="Explicit Windows native entry required",
)


async def test_evaluation_commands_with_selected_file_scope(tmp_path, monkeypatch):
    ctx = await prepare_apply_fixture(tmp_path, monkeypatch, "cli")
    runtime, store = ctx["runtime"], ctx["storage"].metadata
    test = "tests/test_discovery.py"
    (ctx["root"] / test).write_text(
        "def test_discovered():\n    assert 2 == 2\n", encoding="utf-8")
    git(ctx["root"], "add", "--", test)
    git(ctx["root"], "commit", "-m", "ordinary discovery fixture")
    inputs = (*ctx["files"], test)
    task = replace(ctx["task"], delegation_scope=replace(
        ctx["task"].delegation_scope, allowed_paths=inputs),
        metadata={**ctx["task"].metadata, "required_paths": list(inputs)})
    await store.save_task(task)
    graph = replace(ctx["graph"], nodes=tuple(
        replace(n, metadata={**n.metadata, "test_commands": ["pytest", "pytest -q"]})
        if n.type.value == "evaluation" else n for n in ctx["graph"].nodes))
    monkeypatch.setattr(runtime._graph_builder, "build", lambda *a, **kw: graph)
    await runtime.configure_local_git_review(
        task.id, task.actor, executable=ctx["git"],
        executable_sha256="cab4c4eea1d869cf9f7be73868dc9a90ad2df1b1b673e5f8c8714a576c25ea96",
        export_parent=tempfile.mkdtemp(prefix="r04-cwd-regression-"))
    refused = []
    original = runtime._process_broker.run_evaluation_command

    async def denied(call):
        try:
            outcome = await call
        except PermissionError:
            return
        assert not outcome.allowed and not outcome.started

    async def exercise_boundary(workspace, command, **kwargs):
        if not refused:
            for name, changes in [
                ("task", {"task_id": "wrong-task"}),
                ("session", {"session_id": "wrong-session"}),
                ("cwd", {"cwd": workspace.root}),
            ]:
                await denied(original(workspace, command, **{**kwargs, **changes}))
                refused.append(name)
            await denied(original(replace(workspace, id="other-workspace"), command, **kwargs))
            refused.append("workspace")
            await denied(original(workspace, ("python", "-c", "raise SystemExit(99)"), **kwargs))
            refused.append("argv")
            await denied(asyncio.create_task(original(workspace, command, **kwargs)))
            refused.append("owner")
            saved = runtime._frozen_sandboxes.pop(task.id)
            try:
                await denied(original(workspace, command, **kwargs))
                refused.append("sandbox")
            finally:
                runtime._frozen_sandboxes[task.id] = saved
            plain = await runtime._process_broker.run(workspace, command, **kwargs)
            assert not plain.allowed and not plain.started
            refused.append("plain_process")
        return await original(workspace, command, **kwargs)

    monkeypatch.setattr(runtime._process_broker, "run_evaluation_command", exercise_boundary)
    factory = runtime._executor
    node_refused = []

    def executor(metadata):
        instance = factory(metadata)
        agent = instance._agent

        async def check_agent_boundary(*args):
            output = await agent(*args)
            await denied(original(ctx["workspace"], ("pytest",),
                cwd=runtime._frozen_sandboxes[task.id][0].root,
                task_id=task.id, actor=task.actor, scope=task.delegation_scope,
                source=task.source, requested_by=task.actor.user_id,
                resource_scope=task.workspace_id, session_id=task.metadata["session_id"]))
            node_refused.append("non_evaluation")
            return output

        instance._agent = check_agent_boundary
        return instance

    monkeypatch.setattr(runtime, "_executor", executor)
    try:
        await runtime._execute_task(task, artifact_snapshot=None)
        nodes = await store.list_execution_nodes(task.id)
        evaluation = next(n for n in nodes if n.type.value == "evaluation")
        results = evaluation.metadata.get("output", {}).get("results", [])
        (tmp_path / "EVIDENCE.json").write_text(json.dumps(
            {"results": results, "output": evaluation.metadata.get("output"),
             "refused": refused, "node_refused": node_refused}, default=str, indent=2))
        assert results and all(r.get("outcome") == "passed" for r in results), results
        assert len(refused) == 8
        assert node_refused == ["non_evaluation"]
    finally:
        await runtime.stop()
        store.close()


async def test_cli_gateway_creates_owned_local_session(tmp_path, monkeypatch):
    from Sprout.gateway.project_gateway import ProjectGateway
    from Sprout.session.manager import SessionManager

    ctx = await prepare_apply_fixture(tmp_path, monkeypatch, "cli")
    runtime, store = ctx["runtime"], ctx["storage"].metadata
    try:
        created = await ProjectGateway(runtime, transport="cli", default_user="unused").create_task(
            ctx["workspace"].id, "Simplify echo", principal=ctx["principal"],
            delegation_scope=ctx["task"].delegation_scope,
            preparation_paths=tuple(ctx["files"]),
            host_acceptance={"spec": ctx["spec"].to_payload(),
                             "test_path": "tests/frozen.py", "demo_path": "examples/demo.py"})
        persisted = await store.get_task(created.id)
        session_id = persisted.metadata["session_id"]
        restored_manager = SessionManager(runtime._session_store)
        session = await restored_manager.require_owner(session_id, ctx["principal"])
        assert session.id == session_id and session.user_id == persisted.actor.user_id
        assert restored_manager.frozen_principal(session).user_id == persisted.actor.user_id
        with pytest.raises(PermissionError):
            await restored_manager.require_owner(
                session_id, replace(ctx["principal"], user_id="another-owner"))
    finally:
        await runtime.stop()
        store.close()
