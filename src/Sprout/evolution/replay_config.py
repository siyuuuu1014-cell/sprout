"""Credential-free configuration fingerprints for independent experiments."""

from dataclasses import asdict

from Sprout.evolution.replay_oracle import digest


def _without_credentials(value):
    if isinstance(value, dict):
        return {key: _without_credentials(item) for key, item in value.items()
                if key not in {"api_key", "api_key_env", "fallback_api_key_env",
                               "password", "token", "secret", "access_token"}}
    if isinstance(value, (list, tuple)):
        return [_without_credentials(item) for item in value]
    return value


def configuration_contract(settings) -> dict:
    """Persist digests only, never model URLs, policy contents, or MCP env values."""
    security = asdict(settings.security)
    security["audit"].pop("path", None)  # Isolated evidence path is not a permission.
    security.pop("rpc_api_token_env", None)
    security.pop("web_api_token_env", None)
    clients = []
    for client in settings.mcp.clients:
        item = asdict(client)
        item["env"] = sorted(item["env"])  # Values may be credentials; never persisted/hashed.
        clients.append(item)
    return {
        "schema_version": 1,
        "model_fingerprint": digest(_without_credentials(asdict(settings.model))),
        "security_fingerprint": digest(security),
        "tools_fingerprint": digest({"mcp": clients, "skills": asdict(settings.skills)}),
        "execution_fingerprint": digest({"runtime": asdict(settings.runtime),
                                         "context": asdict(settings.context),
                                         "memory": {key: value for key, value in
                                                    asdict(settings.memory).items()
                                                    if key != "home"}}),
    }


def configuration_blockers(contract, settings) -> list[str]:
    if not isinstance(contract, dict) or set(contract) != set(configuration_contract(settings)):
        return ["configuration_contract_missing_or_unsupported"]
    current = configuration_contract(settings)
    if contract.get("schema_version") != 1:
        return ["configuration_contract_missing_or_unsupported"]
    blockers = [key + "_changed" for key in current
                if key != "schema_version" and current[key] != contract[key]]
    if any(client.env for client in settings.mcp.clients):
        # Arbitrary env values cannot be classified as credentials vs behavior.
        # Do not silently allow changed tool behavior under credential rotation.
        blockers.append("mcp_environment_requires_explicit_adapter")
    return blockers
