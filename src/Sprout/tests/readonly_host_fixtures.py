"""Explicit host fixtures, not OS identity or secure-open platform evidence."""

import pytest

from Sprout.execution import file_broker
from Sprout.execution.file_snapshot import read_fd
from Sprout.gateway.identity import Principal
from Sprout.task.models import DelegationScope


async def fixture_analysis(intelligence, workspace, task, *, paths, previous=None):
    """Static/storage component driver over explicitly named fixture inputs.

    This is synthetic test data, never a Runtime capture, read grant or source
    receipt. Delta and stale-fact helpers are exercised separately from the
    pure analyzer; persistence remains the caller's explicit test operation.
    """
    from dataclasses import replace

    from Sprout.workspace.authorized_content import AuthorizedContentView, AuthorizedEntry
    from Sprout.workspace.classifier import classify

    entries = tuple(AuthorizedEntry.synthetic(
        name, (workspace.root / name).read_bytes(), classify(name))
        for name in paths if (workspace.root / name).is_file())
    analysis = await intelligence.analyze_view(workspace, task,
        AuthorizedContentView(workspace.id, entries))
    if previous is not None:
        changed = analysis.fingerprint.changed_resources(previous.fingerprint)
        stale = intelligence._stale_knowledge_ids(previous, set(changed))
        analysis = replace(analysis, changed_resources=changed, stale_knowledge_ids=stale,
            stale_knowledge_items=intelligence._stale_knowledge_items(previous, stale))
    return analysis


async def prompt_from_owned_read(rig, check, *, plan_text="edit main.py", sandbox=None,
                                 agent_metadata=None):
    """Formatter test inside a real owner with an actual READ/PLAN source chain.

    The callback may test formatting synthetic feedback; it issues no EVALUATION
    record or Receipt and never publishes that synthetic feedback to a model.
    """
    from Sprout.agent.base import ModelAgent
    from Sprout.llm.messages import LLMResponse
    from Sprout.orchestration.models import NodeType
    from Sprout.tests.test_r04_b4_restoration import next_node, save_result
    from Sprout.workspace.models import ReadPlan, ResourceRef

    class Model:
        async def chat(self, messages, **kwargs):
            return LLMResponse(content=plan_text)

    rig.runtime.register_agent("prompt-fixture", ModelAgent(Model()), default=True)
    executor = rig.runtime._executor(rig.storage.metadata)
    plan = ReadPlan(task_id=rig.task.id,
                    resources=(ResourceRef(rig.workspace.id, "src/main.py"),))
    read = await next_node(rig, NodeType.READ)
    output = await executor.execute_node(read, rig.workspace, plan, rig.task)
    await save_result(rig, read, output)
    parents = (read.id,)
    if sandbox is not None:
        from unittest.mock import AsyncMock

        # Explicit host-effect double for context/tool-shape tests only. The
        # temporary files are real; no Git isolation or write authority claim.
        executor._sandbox = AsyncMock(return_value={"id": sandbox.id,
            "kind": sandbox.kind, "root": str(sandbox.root)})
        isolated = await next_node(rig, NodeType.SANDBOX, parents)
        value = await executor.execute_node(isolated, rig.workspace, plan, rig.task)
        await save_result(rig, isolated, value)
        parents = (isolated.id,)
    planning = await next_node(rig, NodeType.PLAN, parents)
    planned = await executor.execute_node(planning, rig.workspace, plan, rig.task)
    await save_result(rig, planning, planned)
    node = await next_node(rig, NodeType.AGENT, (planning.id,), agent_metadata)
    with rig.runtime._project_analysis_scope(rig.task, rig.workspace, node):
        await executor._check_model_publication(node, rig.workspace, rig.task)
        result = check(executor, executor._resources(output, rig.workspace), planned["plan"])
        import inspect

        if inspect.isawaitable(result):
            await result


async def native_analysis_subject(runtime, workspace, paths):
    """Real local entry identity/session and finite Task; no platform substitute."""
    from Sprout.gateway.local_identity import current_cli_principal
    from Sprout.message.models import Message

    principal = current_cli_principal()
    session = await runtime._sessions.resolve(
        Message(content="analyze selected files", channel=principal.source), principal=principal)
    task = await runtime.create_task(
        workspace.id, "analyze selected files", actor=principal, source=principal.source,
        execution_mode="read_only", metadata={"required_paths": list(paths),
                                              "session_id": session.id},
        delegation_scope=DelegationScope(
            allowed_paths=tuple(str(workspace.root / path) for path in paths),
            allowed_actions=frozenset({"tool.invoke", "file.read"}),
        ),
    )
    return {"principal": principal, "analysis_task": task}


@pytest.fixture
def authorized_read_host(tmp_path, monkeypatch):
    """Issue a controlled server subject and bounded selection for host tests.

    Keep real Policy/Scope/Broker checks. Only the platform open primitive is a
    temporary-file substitute, as in B2 tests; no directory discovery is mocked.
    """
    root = tmp_path.resolve()

    def boundary(workspace_root, target, operation, authorize, **kwargs):
        assert target.resolve().is_relative_to(root)
        assert kwargs.get("content") is not None, "Only snapshot reads are modeled"
        authorize()
        with target.open("rb") as stream:
            return read_fd(stream.fileno(), kwargs["content"])

    monkeypatch.setattr(file_broker, "file_operation", boundary)

    def issue(path, *, source):
        assert path.resolve().is_relative_to(root)
        principal = Principal(user_id="controlled-host-owner", source=source, authenticated=True)
        scope = DelegationScope(
            allowed_actions=frozenset({"tool.invoke", "file.read"}),
            allowed_paths=(str(path),),
        )
        return principal, scope

    return issue
