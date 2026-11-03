"""A real CODE admission refusal must not terminate the CLI conversation."""
import asyncio
import json

import pytest

from Sprout.cli.commands import chat
from Sprout.llm.messages import LLMResponse
from Sprout.runtime.intent_analysis import response_example
from Sprout.tests.test_r04_b2_capture import captured as captured
from Sprout.tests.test_r04_b4_cli_analysis import cli as cli
from Sprout.tests.test_r04_pa2_b1_binding import rig as rig


@pytest.mark.parametrize("terminal", [False, True])
async def test_real_code_scope_denial_returns_to_same_session(cli, monkeypatch, terminal, capsys):
    (cli.rig.root / ".git").mkdir()
    cli.state.terminal = terminal
    cli.state.inputs = ["修复 src/main.py 的错误", "你好，继续对话"]
    actual_chat = cli.model.chat
    classifications = iter(("task", "conversation"))

    async def model_chat(messages, **kwargs):
        if "Classify the user's intent" in str(messages[0].content):
            intent = next(classifications)
            cli.state.classification += 1
            mode = "code_modification" if intent == "task" else "conversation"
            value = json.loads(response_example(True))
            value.update(intent=intent, confidence=0.99)
            value["semantic"].update(
                action_mode=mode, candidates=[{"intent": intent, "action_mode": mode}],
                ambiguity_type="none", missing_information=[], needs_clarification=False,
                question="")
            return LLMResponse(content=json.dumps(value))
        return await actual_chat(messages, **kwargs)

    monkeypatch.setattr(cli.model, "chat", model_chat)
    # Exercise the legacy no-provider refusal; the interactive preparation
    # cancellation is independently tested by the new CODE adapter tests.
    monkeypatch.setattr(chat, "_cli_code_task_provider", lambda *args: None)
    await cli.run()
    await asyncio.gather(*tuple(cli.rig.runtime._background_tasks))
    cli.create.assert_awaited_once()
    task = cli.create.call_args.kwargs
    assert task["metadata"]["session_id"] == cli.rig.session.id
    assert "delegation_scope" not in task  # A denial is not converted into a grant.
    assert "project_auto_discovery_unavailable" in capsys.readouterr().out
    assert "CLI_PROJECT_MAIN_RESULT" in cli.state.display
    turns = await cli.rig.runtime.history(cli.rig.session.id, principal=cli.rig.principal)
    assert any(turn.content == "你好，继续对话" for turn in turns)
    assert not cli.captured.opens
    cli.claim.assert_not_called()
