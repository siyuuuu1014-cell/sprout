"""Unresolved public online entry: a routing hint supplies no Task authority."""
import json
from unittest.mock import Mock

from Sprout.agent.executor import ActionExecutor
from Sprout.agent.loop import AgentLoop
from Sprout.llm.messages import LLMResponse
from Sprout.message.models import Message
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


async def test_online_analysis_request_reaches_authorized_project_result(
    rig, captured, monkeypatch,
):
    from Sprout.runtime.intent_analysis import response_example

    semantic = json.loads(response_example(True))
    semantic["semantic"].update(
        action_mode="read_only_analysis",
        candidates=[{"intent": "workspace", "action_mode": "read_only_analysis"}],
        ambiguity_type="none", missing_information=[], needs_clarification=False, question="")
    seen = []
    class Model:
        name = "online-controlled"
        async def chat(self, messages, **kwargs):
            if "Classify the user's intent" in str(messages[0].content):
                return LLMResponse(content=json.dumps(semantic))
            seen.extend(messages)
            return LLMResponse(content="controlled response")
    model = Model()
    rig.runtime.models.register(model, default=True)
    rig.runtime.register_agent("online", AgentLoop(model, executor=ActionExecutor(rig.executor)),
                               default=True)
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: Mock())
    await rig.runtime.handle(Message(
        "请分析项目结构", channel="cli", user_id="owner", session_id=rig.session.id,
        metadata={"workspace_path": str(rig.root / "src")}), principal=rig.principal)
    assert not captured.opens
    assert any(str(m.role) == "tool" and "project_analysis_scope_unavailable" in m.content
               for m in seen)
    # Deliberate RED: success requires an authoritative analysis Task admission
    # contract, not selecting a stored Task merely because Session ids match.
    assert any(str(m.role) == "tool" and "main" in m.content for m in seen)
