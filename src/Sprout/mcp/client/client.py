"""MCP Client: let Sprout use external MCP servers as tool sources.

External MCP Server -> Sprout MCP Client -> MCPToolAdapter -> ToolRegistry -> Agent
The agent only ever sees uniform ToolSpec objects.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, replace
from typing import TYPE_CHECKING
from uuid import uuid4

from Sprout.mcp.adapter.tool import MCPTool, MCPToolAdapter, _canonical, descriptor_fingerprint
from Sprout.mcp.client.connection import MCPConnection

if TYPE_CHECKING:
    from Sprout.config.settings import MCPSettings, Settings
    from Sprout.runtime.runtime import Runtime

logger = logging.getLogger("sprout.mcp.client")


class MCPClientManager:
    """Starts, tracks, and stops external MCP server connections."""

    def __init__(self, connections: dict[str, MCPConnection] | None = None) -> None:
        self._connections: dict[str, MCPConnection] = connections or {}
        self._settings = None
        self._configuration = ""
        self._issued: dict[int, dict] = {}

    @classmethod
    def from_settings(cls, settings: MCPSettings) -> MCPClientManager:
        names = [client.name for client in settings.clients]
        if (any(not isinstance(name, str) or not name or len(name) > 128 for name in names)
                or len(set(names)) != len(names)):
            raise ValueError("Invalid or ambiguous configured MCP server names")
        for client in settings.clients:
            client.__post_init__()
        connections = {
            client.name: MCPConnection(
                name=client.name, command=client.command, args=client.args, env=client.env
            )
            for client in settings.clients
        }
        manager = cls(connections)
        manager._settings = settings
        manager._configuration = _canonical(asdict(settings))
        return manager

    def _revoke(self, tool: MCPTool) -> None:
        self._issued.pop(id(tool), None)

    def _revoke_connection(self, connection: MCPConnection) -> None:
        for key, record in list(self._issued.items()):
            if record["connection"] is connection:
                del self._issued[key]

    def _classification(self, tool: MCPTool) -> str | None:
        record = self._issued.get(id(tool))
        if record is None or record["tool"] is not tool:
            return None
        connection = record["connection"]
        try:
            configured = next(client for client in self._settings.clients
                              if client.name == connection.name)
            valid = (
                self._configuration == _canonical(asdict(self._settings))
                and self._connections.get(connection.name) is connection
                and tool.connection is connection and connection._started is True
                and connection.session is record["session"]
                and (connection._command, tuple(connection._args), connection._env)
                == (configured.command, tuple(configured.args), configured.env)
                and tool.remote_name == record["operation"]
                and _canonical(asdict(tool.spec)) == record["spec"]
                and descriptor_fingerprint(record["descriptor"]) == record["digest"]
            )
        except (AttributeError, RuntimeError, StopIteration, TypeError, ValueError):
            valid = False
        if not valid:
            self._revoke(tool)
            return None
        return record["key"]

    def _classify_discovered(self, connection, remote, tool) -> None:
        # Only from_settings establishes the local configuration authority.
        if self._settings is None or not connection._started:
            return
        try:
            if self._configuration != _canonical(asdict(self._settings)):
                return
            configured = next(client for client in self._settings.clients
                              if client.name == connection.name)
            declarations = [entry for entry in configured.tool_risks
                            if entry["operation"] == remote.name]
            if len(declarations) != 1:
                return
            declaration = declarations[0]
            digest = descriptor_fingerprint(remote)
            if digest != declaration["descriptor_sha256"]:
                return
            tool.spec = replace(tool.spec, risk_level=declaration["risk"])
            tool._manager = self
            self._issued[id(tool)] = {
                "tool": tool, "connection": connection, "session": connection.session,
                "operation": remote.name, "descriptor": remote, "digest": digest,
                "spec": _canonical(asdict(tool.spec)), "key": "mcp-risk-v1:" + uuid4().hex,
            }
            self._classification(tool)  # Validate launch identity before issuance can be used.
        except (AttributeError, RuntimeError, StopIteration, TypeError, ValueError):
            self._revoke(tool)

    @property
    def connections(self) -> dict[str, MCPConnection]:
        return dict(self._connections)

    def connection(self, name: str) -> MCPConnection:
        return self._connections[name]

    async def start(self) -> None:
        self._issued.clear()
        for connection in self._connections.values():
            try:
                await connection.start()
            except Exception:  # noqa: BLE001 - one bad server must not block startup
                logger.exception("Failed to start MCP connection %r", connection.name)

    async def stop(self) -> None:
        self._issued.clear()  # Revoke before awaiting connection teardown, even if teardown fails.
        for connection in self._connections.values():
            await connection.stop()

    async def collect_tools(self) -> list[MCPTool]:
        """List every remote tool, adapted to the uniform Tool interface."""
        self._issued.clear()  # Re-discovery/config reload cannot preserve old approvals/bindings.
        adapter = MCPToolAdapter()
        tools: list[MCPTool] = []
        for connection in self._connections.values():
            try:
                remote_tools = await connection.list_tools()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to list tools from %r", connection.name)
                continue
            counts = Counter(remote.name for remote in remote_tools)
            for remote in remote_tools:
                tool = adapter.to_tool(connection, remote)
                if counts[remote.name] == 1:
                    self._classify_discovered(connection, remote, tool)
                tools.append(tool)
        counts = Counter(tool.spec.name for tool in tools)
        for tool in tools:
            if counts[tool.spec.name] != 1:
                self._revoke(tool)
        return tools


async def attach_mcp_clients(runtime: Runtime, settings: Settings) -> MCPClientManager | None:
    """Entry-point helper: spawn configured external servers, register their tools.

    Called by CLI/Web/MCP entries after ``create_runtime``; the runtime factory
    itself stays free of MCP imports.
    """
    previous = getattr(runtime, "_mcp_client_manager", None)
    if previous is not None:
        await previous.stop()
    runtime._mcp_client_manager = None
    if not settings.mcp.clients:
        return None
    manager = MCPClientManager.from_settings(settings.mcp)
    runtime._mcp_client_manager = manager
    await manager.start()
    registered = 0
    for tool in await manager.collect_tools():
        runtime.tools.register(tool)
        registered += 1
    logger.info("Registered %d MCP tools from %d server(s)", registered, len(manager.connections))
    return manager
