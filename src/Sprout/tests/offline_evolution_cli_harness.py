"""Test-only offline model/authorization adapter around the real CLI.

Settings, databases, entry points, worker bindings and tools are unmodified.
This is not production-policy or real-model validation. Importing this module
does not install the adapter; only its explicit test process entry does so.
"""

from Sprout.agent.executor import ActionExecutor
from Sprout.execution.file_broker import FileBroker
from Sprout.execution.process_broker import ProcessBroker
from Sprout.llm.messages import LLMResponse, Role
from Sprout.security.policy import SecurityPolicy
from Sprout.tests.test_isolated_coding_temporal_integration import (
    CodingFixturePolicy,
    ScriptedAdditionModel,
)
from Sprout.tools.executor import ToolExecutor

CANDIDATE_NAME = "addition-procedure"
CANDIDATE_BODY = "Implement app.add with a + b and run the frozen checks. " * 3


class OfflineProcessModel(ScriptedAdditionModel):
    """Choose the fixed fixture from current messages, not previous process memory."""

    async def chat(self, messages, *, tools=()):
        if any(message.role == Role.TOOL and message.name == "isolated_coding_command"
               for message in messages):
            return LLMResponse(content="Persisted fixture command completed; no new command",
                               finish_reason="stop")
        if "isolated_coding_command" in {tool.name for tool in tools}:
            index = "\n".join(message.content or "" for message in messages
                              if message.role == Role.SYSTEM)
            self.skill_name = CANDIDATE_NAME if CANDIDATE_NAME in index else None
            self.skill_body = CANDIDATE_BODY
        return await super().chat(messages, tools=tools)


def install_fixture_runtime(*, allow_coding_command=False):
    from Sprout.runtime import factory

    original = factory.create_runtime

    def create(settings):
        runtime = original(settings)
        policy = CodingFixturePolicy()
        runtime._process_broker = ProcessBroker(policy, approvals=runtime.approvals)
        runtime._file_broker = FileBroker(policy)
        agent = runtime.agents.get(settings.runtime.default_agent)
        agent.model = OfflineProcessModel()
        # The coding command must still park for a persisted human decision.
        agent.executor = ActionExecutor(ToolExecutor(tools=runtime.tools, policy_engine=policy,
            approvals=runtime.approvals, policy=SecurityPolicy(
                always_allow=frozenset({"skill_view", "isolated_coding_command"}
                    if allow_coding_command else {"skill_view"}))))
        return runtime

    factory.create_runtime = create


if __name__ == "__main__":
    from Sprout.cli.app import main

    install_fixture_runtime()
    main()
