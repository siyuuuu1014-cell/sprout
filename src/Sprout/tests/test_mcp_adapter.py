"""MCP adapter and server-surface tests.

The adapter is what makes external MCP tools look like native Sprout tools:
remote descriptors become prefixed ToolSpec objects and invoke() is proxied
through the connection.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from Sprout.mcp.adapter.tool import MCPTool, MCPToolAdapter, _content_to_text
from Sprout.mcp.server.server import build_server, server_definitions
from Sprout.tests.conftest import build_runtime
from Sprout.tools.result import ToolExecutionUnconfirmed


class FakeConnection:
    """Stands in for MCPConnection; records calls and returns canned results."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.name = "fake"
        self.calls: list[tuple[str, dict]] = []
        self._result = result
        self._error = error

    async def call_tool(self, name: str, arguments: dict) -> object:
        self.calls.append((name, arguments))
        if self._error is not None:
            raise self._error
        return self._result


def _remote_tool(name: str = "search", schema: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description="Search the filesystem",
        inputSchema=schema if schema is not None else {"type": "object"},
    )


# -- adapter ------------------------------------------------------------------


def test_to_tool_spec_prefixes_server_name() -> None:
    spec = MCPToolAdapter().to_tool_spec(_remote_tool(), server_name="fs")
    assert spec.name == "fs_search"
    assert spec.risk_level == "medium"
    assert spec.input_schema == {"type": "object"}


def test_to_tool_spec_without_server_name_keeps_bare_name() -> None:
    spec = MCPToolAdapter().to_tool_spec(_remote_tool("bare"))
    assert spec.name == "bare"


def test_to_tool_spec_falls_back_to_placeholder_description() -> None:
    tool = SimpleNamespace(name="x", description=None, inputSchema=None)
    spec = MCPToolAdapter().to_tool_spec(tool, server_name="fs")
    assert "fs" in spec.description
    assert spec.input_schema == {}


def test_to_tool_wraps_connection_and_spec() -> None:
    connection = FakeConnection()
    tool = MCPToolAdapter().to_tool(connection, _remote_tool())
    assert isinstance(tool, MCPTool)
    assert tool.spec.name == "fake_search"
    assert tool.remote_name == "search"


# -- MCPTool.invoke --------------------------------------------------------------


@pytest.fixture
async def trusted_remote(monkeypatch):
    from Sprout.mcp.adapter.tool import descriptor_fingerprint
    from Sprout.mcp.client import client as client_module
    from Sprout.tests.test_r04_c1_mcp_trusted_wiring import (
        IDENTITY,
        config,
        executor_for,
        issued,
    )
    from Sprout.tests.test_r04_c1_mcp_trusted_wiring import (
        FakeConnection as ManagerTransport,
    )

    class Transport(ManagerTransport):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.descriptors = [_remote_tool()]

    monkeypatch.setattr(client_module, "MCPConnection", Transport)
    # A known test descriptor is reviewed by the Host before discovery. The
    # transport response cannot enroll itself as trusted.
    declaration = config(name="fake", risk="low", operation="search",
                         digest=descriptor_fingerprint(_remote_tool()))
    manager, connection, tool = await issued(configured=declaration)
    executor = executor_for(tool)

    async def invoke(arguments):
        return await executor.execute(tool.spec.name, arguments, **IDENTITY)

    try:
        yield connection, invoke
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_invoke_proxies_success(trusted_remote) -> None:
    result_obj = SimpleNamespace(isError=False, content=[SimpleNamespace(text="found 2 files")])
    connection, invoke = trusted_remote
    connection.call_tool.return_value = result_obj

    result = await invoke({"path": "/tmp"})
    assert result.ok
    assert result.content == "found 2 files"
    assert result.data == {"server": "fake"}
    connection.call_tool.assert_awaited_once_with("search", {"path": "/tmp"})


@pytest.mark.asyncio
async def test_invoke_maps_remote_error_to_failure(trusted_remote) -> None:
    result_obj = SimpleNamespace(isError=True, content=[SimpleNamespace(text="boom")])
    connection, invoke = trusted_remote
    connection.call_tool.return_value = result_obj

    result = await invoke({})
    assert not result.ok
    assert "boom" in result.error
    connection.call_tool.assert_awaited_once_with("search", {})


@pytest.mark.asyncio
async def test_invoke_maps_transport_exception_to_failure(trusted_remote) -> None:
    connection, invoke = trusted_remote
    connection.call_tool.side_effect = RuntimeError("subprocess died")
    with pytest.raises(ToolExecutionUnconfirmed, match="mcp_remote_execution_unconfirmed"):
        await invoke({})
    connection.call_tool.assert_awaited_once_with("search", {})
    # Transport loss revokes the live binding. An explicit subsequent request
    # cannot silently execute again or reinterpret UNKNOWN as a retryable error.
    assert not (await invoke({})).ok
    connection.call_tool.assert_awaited_once()


# -- content extraction -----------------------------------------------------------


def test_content_to_text_variants() -> None:
    assert _content_to_text("plain") == "plain"
    assert _content_to_text([SimpleNamespace(text="a"), SimpleNamespace(text="b")]) == "a\nb"
    assert _content_to_text(SimpleNamespace(text="solo")) == "solo"


# -- server surface -----------------------------------------------------------------


def test_server_definitions_expose_safe_surface_only() -> None:
    definitions = server_definitions()
    tool_names = {tool["name"] for tool in definitions["tools"]}
    assert tool_names == {
        "sprout_message",
        "sprout_session_create",
        "sprout_session_history",
        "sprout_skill_list",
        "sprout_knowledge_search",
        "workspace_scan",
        "workspace_query",
        "task_create",
        "task_execute",
        "task_changes",
        "task_plan",
        "sandbox_diff",
        "sandbox_test",
        "trajectory_query",
        "proposal_show",
        "proposal_request_approval",
        "proposal_reject",
        "proposal_apply",
    }
    # Approval can be requested over MCP but is never decided here (spec 11.5).
    assert "proposal_approve" not in tool_names
    # No storage writes or evolution publishing over MCP.
    assert not any("publish" in name or "storage" in name for name in tool_names)
    assert definitions["resources"]
    assert definitions["prompts"]


def test_build_server_assembles_around_runtime() -> None:
    runtime, _ = build_runtime()
    server = build_server(runtime)
    assert server is not None
