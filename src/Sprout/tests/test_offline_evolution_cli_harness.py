from types import SimpleNamespace

from Sprout.llm.messages import LLMMessage, Role
from Sprout.tests.offline_evolution_cli_harness import (
    CANDIDATE_BODY,
    CANDIDATE_NAME,
    OfflineProcessModel,
)


async def test_fresh_process_model_uses_current_skill_index_and_persisted_tool_result():
    tools = (SimpleNamespace(name="isolated_coding_command"), SimpleNamespace(name="skill_view"))
    messages = [LLMMessage(role=Role.SYSTEM, content=CANDIDATE_NAME)]
    model = OfflineProcessModel()
    first = await model.chat(messages, tools=tools)
    assert first.tool_calls[0].name == "skill_view"
    messages.append(LLMMessage(role=Role.TOOL, name="skill_view", content=CANDIDATE_BODY))
    second = await model.chat(messages, tools=tools)
    assert second.tool_calls[0].name == "isolated_coding_command"
    # No counters or agent objects are transferred to the new instance.
    fresh = OfflineProcessModel()
    resumed = await fresh.chat([LLMMessage(role=Role.TOOL, name="isolated_coding_command",
                                         content="completed")], tools=tools)
    assert not resumed.tool_calls and fresh.commands == 0
