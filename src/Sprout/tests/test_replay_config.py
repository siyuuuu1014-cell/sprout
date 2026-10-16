from copy import deepcopy

import pytest

from Sprout.config.loader import default_settings
from Sprout.evolution.replay_config import configuration_blockers, configuration_contract


def test_credentials_rotate_without_changing_contract():
    settings = default_settings()
    settings.model.api_key = "old-secret"
    settings.model.providers = ({"provider": "example", "model": "fixed",
                                 "api_key": "old-provider-secret"},)
    contract = configuration_contract(settings)
    settings.model.api_key = "new-secret"
    settings.model.api_key_env = "NEW_KEY"
    settings.model.providers = ({"provider": "example", "model": "fixed",
                                 "api_key": "new-provider-secret"},)
    assert configuration_blockers(contract, settings) == []
    assert "secret" not in str(contract)


@pytest.mark.parametrize("group,field,value,blocker", [
    ("model", "model", "different", "model_fingerprint_changed"),
    ("model", "temperature", 0.7, "model_fingerprint_changed"),
    ("security", "require_approval", False, "security_fingerprint_changed"),
    ("runtime", "max_tool_steps", 90, "execution_fingerprint_changed"),
    ("skills", "search_enabled", False, "tools_fingerprint_changed"),
])
def test_config_changes_block_resume(group, field, value, blocker):
    settings = default_settings()
    contract = configuration_contract(settings)
    changed = deepcopy(settings)
    setattr(getattr(changed, group), field, value)
    assert blocker in configuration_blockers(contract, changed)


def test_missing_contract_not_backfilled():
    assert configuration_blockers(None, default_settings()) == [
        "configuration_contract_missing_or_unsupported"
    ]


def test_arbitrary_mcp_environment_fails_closed_without_persisting_values():
    from Sprout.config.settings import MCPClientSettings

    settings = default_settings()
    settings.mcp.clients = (MCPClientSettings(name="external", command="tool",
                                             env={"VALUE": "private-value"}),)
    contract = configuration_contract(settings)
    assert "private-value" not in str(contract)
    assert "mcp_environment_requires_explicit_adapter" in configuration_blockers(contract, settings)
