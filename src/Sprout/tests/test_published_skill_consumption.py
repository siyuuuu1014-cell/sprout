"""Offline wiring proof, not evidence that a real model becomes more capable."""

import pytest

from Sprout.config.loader import default_settings
from Sprout.evolution.isolated_replay import isolated_settings
from Sprout.llm.messages import LLMResponse, Role, ToolCall
from Sprout.message.models import Message
from Sprout.runtime.factory import create_runtime
from Sprout.skills.models import Skill
from Sprout.skills.repository import SkillRepository

MARKER = "Procedure marker: offline-consumption-fixture"
BODY = "Inspect evidence before retrying.\n\nRead actual output before deciding.\n" + MARKER


class SkillConsumerModel:
    name = "explicit-offline-skill-consumer"
    model = name

    def __init__(self, name, expected_body):
        self.skill_name, self.expected_body = name, expected_body
        self.calls = 0
        self.saw_index = False
        self.saw_body = False

    async def chat(self, messages, *, tools=()):
        self.calls += 1
        if self.calls == 1:
            indexes = [message.content or "" for message in messages
                       if message.role == Role.SYSTEM
                       and "Available skills" in (message.content or "")]
            self.saw_index = any(self.skill_name in text for text in indexes)
            if not self.saw_index:
                return LLMResponse(content="No published procedure available.")
            assert all(MARKER not in text for text in indexes), "Full body leaked into L0 index"
            assert "skill_view" in {tool.name for tool in tools}
            return LLMResponse(tool_calls=(ToolCall("read-published-skill", "skill_view",
                {"name": self.skill_name}),), finish_reason="tool_calls")
        assert self.calls == 2
        results = [message for message in messages if message.role == Role.TOOL
                   and message.name == "skill_view"
                   and message.tool_call_id == "read-published-skill"]
        assert len(results) == 1
        self.saw_body = self.expected_body in (results[0].content or "")
        assert self.saw_body, results[0].content
        return LLMResponse(content=MARKER)


async def consume_skill(runtime, name, expected_body):
    """Use the real Runtime turn, context builder, agent loop and skill_view."""
    consumer = SkillConsumerModel(name, expected_body)
    runtime.agents.get(runtime._settings.runtime.default_agent).model = consumer
    response = await runtime.handle(Message(content="Explain the verification procedure.",
        user_id="offline-skill-consumption-user", channel="cli",
        metadata={"intent": "conversation"}))
    return consumer, response


@pytest.mark.parametrize("published", [False, True])
async def test_loaded_skill_reaches_real_runtime_turn(tmp_path, published):
    settings = isolated_settings(tmp_path, default_settings())
    settings.model.provider = "echo"
    if published:
        SkillRepository(settings.skills_dir).save(Skill(
            name="fixture-procedure", version="1", instructions=BODY))
    runtime = create_runtime(settings)
    try:
        model, response = await consume_skill(runtime, "fixture-procedure", BODY)
        assert model.saw_index is published and model.saw_body is published
        assert model.calls == (2 if published else 1)
        assert response.content == (MARKER if published else "No published procedure available.")
    finally:
        await runtime.storage.close()
