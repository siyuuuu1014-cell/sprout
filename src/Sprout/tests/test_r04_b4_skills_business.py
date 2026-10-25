"""Existing local instruction/resource Skill -> legal tool -> actual Runtime reply."""
import json
from unittest.mock import Mock

from Sprout.agent.executor import ActionExecutor
from Sprout.agent.loop import AgentLoop
from Sprout.gateway.identity import Principal
from Sprout.llm.messages import LLMResponse, ToolCall
from Sprout.message.models import Message
from Sprout.runtime.intent_analysis import response_example
from Sprout.skills.index import SkillIndex
from Sprout.skills.layout import index_path
from Sprout.skills.models import SkillRecord
from Sprout.skills.registry import create_skill_registry
from Sprout.tests.conftest import EchoTool, build_runtime


async def test_local_skill_runtime_business_reply(tmp_path, monkeypatch):
    directory = tmp_path / "skills"
    folder = directory / "guide"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: guide\ndescription: echo guidance\n---\nUse echo_tool to answer.\n",
        encoding="utf-8")
    (folder / "usage.txt").write_text("LOCAL_SKILL_RESOURCE", encoding="utf-8")
    skills = create_skill_registry(directory)
    skill = skills.get("guide")
    SkillIndex(index_path(directory)).save([SkillRecord(
        name=skill.name, version=skill.version, description=skill.description, trust=skill.trust)])
    seen, calls = [], []
    actions = [("skill_search", {"query": "echo guidance"}),
               ("skill_view", {"name": "guide"}),
               ("skill_view", {"name": "guide", "path": "usage.txt"}),
               ("echo_tool", {"text": "LOCAL_SKILL_TOOL_RESULT"})]

    class Model:
        name = "skill-business-model"

        async def chat(self, messages, **kwargs):
            if "response_example" in str(messages) or "semantic" in str(messages):
                value = json.loads(response_example(True))
                value.update(intent="chat", confidence=0.99)
                value["semantic"].update(
                    action_mode="conversation", candidates=[{
                        "intent": "chat", "action_mode": "conversation"}],
                    ambiguity_type="none", missing_information=[],
                    needs_clarification=False, question="")
                return LLMResponse(content=json.dumps(value))
            seen.append(list(messages))
            if len(calls) < len(actions):
                name, arguments = actions[len(calls)]
                calls.append(name)
                return LLMResponse(tool_calls=(ToolCall(
                    id=str(len(calls)), name=name, arguments=arguments),))
            return LLMResponse(content="RUNTIME_LOCAL_SKILL_COMPLETED")

    model = Model()
    runtime, executor = build_runtime(model=model)
    runtime.skills = skills
    runtime._skills_dir = directory
    runtime._register_skill_tools()
    echo = EchoTool()
    runtime.tools.register(echo)
    runtime.register_agent("skill-business", AgentLoop(
        model, executor=ActionExecutor(executor), max_steps=6), default=True)
    monkeypatch.setattr("Sprout.agent.loop.get_agent_step_store", lambda: Mock())
    result = await runtime.handle(Message(content="please use the guide skill", channel="cli"),
        principal=Principal(user_id="owner", source="cli", authenticated=True))
    assert result.content == "RUNTIME_LOCAL_SKILL_COMPLETED"
    assert calls == [name for name, _ in actions]
    assert echo.seen == [{"text": "LOCAL_SKILL_TOOL_RESULT"}]
    assert "LOCAL_SKILL_RESOURCE" in repr(seen[-1])
    assert "Use echo_tool" in repr(seen[-1])
