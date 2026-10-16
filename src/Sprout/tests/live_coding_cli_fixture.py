"""Explicit paid-trial adapter for formal CLI commands, never production installed."""

import asyncio
import os
from dataclasses import replace


def install():
    if os.getenv("SPROUT_DEEPSEEK_LIVE_CODING") != "1":
        raise ValueError("Explicit live experiment authorization required")
    limit = int(os.environ["SPROUT_LIVE_REQUEST_LIMIT"])
    if not 1 <= limit <= 20:
        raise ValueError("Explicit request limit required")
    from Sprout.agent.executor import ActionExecutor
    from Sprout.config import loader
    from Sprout.execution.file_broker import FileBroker
    from Sprout.execution.process_broker import ProcessBroker
    from Sprout.runtime import factory
    from Sprout.security.policy import SecurityPolicy
    from Sprout.tests.live_coding_policy import LiveCodingPolicy
    from Sprout.tests.live_model_budget import LiveModelBudget
    from Sprout.tools.executor import ToolExecutor

    original_load, original_create = loader.load_settings, factory.create_runtime

    def load(path=None):
        settings = original_load(path)
        settings.model = replace(settings.model, max_output_tokens=8192)
        return settings

    def create(settings):
        runtime = original_create(settings)
        policy = LiveCodingPolicy()
        runtime._process_broker = ProcessBroker(policy, approvals=runtime.approvals)
        runtime._file_broker = FileBroker(policy)
        model = settings.model
        provider = factory._build_provider(model.provider, model.model, model.base_url,
                                           model.api_key_env, model)
        agent = runtime.agents.get(settings.runtime.default_agent)
        agent.model = LiveModelBudget(provider, limit=limit)
        agent.executor = ActionExecutor(ToolExecutor(tools=runtime.tools, policy_engine=policy,
            approvals=runtime.approvals, policy=SecurityPolicy(
                always_allow=frozenset({"isolated_coding_command", "skill_view"}))))
        return runtime

    loader.load_settings, factory.create_runtime = load, create


if __name__ == "__main__":
    from Sprout.cli.app import main
    from Sprout.tests.commit_worker_signal_harness import controlled_run

    install()
    asyncio.run = controlled_run
    main()
