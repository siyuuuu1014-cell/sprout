"""R04 B1 needs the original server Principal, not a lossy reconstructed one."""
from unittest.mock import AsyncMock, Mock

from Sprout.agent.base import AgentResult
from Sprout.message.models import Message
from Sprout.tests.conftest import build_runtime
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


async def test_task_routing_preserves_real_server_principal(rig, monkeypatch):
    (rig.root / ".git").mkdir()
    create = AsyncMock(return_value=rig.task)
    submit = AsyncMock()
    monkeypatch.setattr(rig.runtime, "create_task", create)
    monkeypatch.setattr(rig.runtime, "submit_task", submit)
    monkeypatch.setattr(rig.runtime, "start_worker", Mock())
    result = await rig.runtime._handle_task(Message(
        "fix a bug", user_id="owner", metadata={"workspace_id": rig.workspace.id}),
        rig.session, principal=rig.principal)
    assert create.call_args.kwargs["actor"] == rig.principal
    assert result.metadata["task_id"] == rig.task.id
    submit.assert_awaited_once_with(rig.task)


async def test_ordinary_chat_without_verified_history_keeps_isolated_context():
    contexts = []
    class Agent:
        async def run(self, message, context):
            contexts.append(context)
            return AgentResult("ordinary reply")
    runtime, _ = build_runtime(agent=Agent())
    result = await runtime.handle(Message("hello"))
    assert result.content == "ordinary reply"
    assert contexts[0].memory == ()
    assert contexts[0].workspace is None
