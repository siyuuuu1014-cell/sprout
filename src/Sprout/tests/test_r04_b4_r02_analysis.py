"""R02 routing hints reach a B4 tool only inside a real commissioned subject.

This is not a claim that unbound public online chat creates a trusted Task.
"""
import json
from unittest.mock import AsyncMock, Mock

import pytest

from Sprout.agent.executor import ActionExecutor
from Sprout.agent.loop import AgentLoop
from Sprout.context.context import AgentContext
from Sprout.llm.messages import LLMResponse
from Sprout.message.models import Message
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_pa2_b1_binding import node_scope
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.mark.parametrize("analysis", [True, False])
async def test_existing_r02_classifier_to_bound_agent_loop(rig, captured, monkeypatch, analysis):
    from Sprout.runtime.intent_analysis import response_example

    semantic = json.loads(response_example(True))
    semantic.update(intent="workspace" if analysis else "conversation", confidence=0.99)
    semantic["semantic"].update(
        action_mode="read_only_analysis" if analysis else "conversation",
        candidates=[{"intent": semantic["intent"],
                     "action_mode": "read_only_analysis" if analysis else "conversation"}],
        ambiguity_type="none", missing_information=[], needs_clarification=False, question="")
    classifier = AsyncMock()
    classifier.chat.return_value = LLMResponse(content=json.dumps(semantic))
    monkeypatch.setattr(type(rig.runtime.models), "default", lambda self: classifier)
    message = Message("请分析项目结构" if analysis else "你好，今天怎么样", user_id="owner",
                      session_id=rig.session.id,
                      metadata={"workspace_path": str(rig.root / "src")})
    context = AgentContext(session=rig.session, tools={})
    result = await rig.runtime._classify_intent_with_llm(message, context)
    assert result["intent"] == ("workspace" if analysis else "conversation")
    enriched, context = await rig.runtime._recognize_intent(message, context, classification=result)
    before = await rig.storage.metadata.list_tasks()
    if not analysis:
        assert "resume_tool_calls" not in context.metadata
        assert await rig.runtime._dispatch_task_intent(enriched, context, rig.session) is None
        assert await rig.storage.metadata.list_tasks() == before
        return
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: Mock())
    seen = []
    class Model:
        async def chat(self, messages, **kwargs):
            seen.extend(messages)
            return LLMResponse(content="project main analyzed")
    with node_scope(rig):
        tool = await rig.runtime._issue_project_analysis_tool(paths=("src/main.py",))
        context = AgentContext(session=rig.session, tools={tool.spec.name: tool},
                               metadata=context.metadata)
        answer = await AgentLoop(Model(), executor=ActionExecutor(rig.executor)).run(
            enriched, context)
    assert answer.content == "project main analyzed"
    assert any(str(m.role) == "tool" and "main" in m.content for m in seen)
    assert captured.opens == ["src/main.py"]
