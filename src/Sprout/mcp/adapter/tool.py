"""MCP tool adapter: remote MCP tools become uniform Sprout tools.

External MCP Tool -> MCPToolAdapter -> ToolSpec -> ToolRegistry -> AgentContext
Python, HTTP, and MCP tools all end up behind the same interface.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from Sprout.tools.result import ToolExecutionUnconfirmed, ToolResult
from Sprout.tools.spec import ToolSpec

if TYPE_CHECKING:
    from Sprout.mcp.client.connection import MCPConnection


def _canonical(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > 262144:
        raise ValueError("MCP binding payload exceeds limit")
    return encoded


def descriptor_fingerprint(remote: Any) -> str:
    """Consistency digest only; computing it does not issue risk authority."""
    if (not isinstance(remote.name, str) or not isinstance(remote.description, (str, type(None)))
            or not isinstance(remote.inputSchema, (Mapping, type(None)))):
        raise ValueError("Invalid MCP descriptor")
    payload = {"name": remote.name, "description": remote.description or "",
               "input_schema": dict(remote.inputSchema or {})}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _classification(tool: MCPTool) -> str | None:
    # Manager registry, not fields on a caller-created dataclass, is authority.
    from Sprout.mcp.client.client import MCPClientManager

    manager = tool._manager
    if type(manager) is not MCPClientManager:
        return None
    return manager._classification(tool)


def _remote_arguments(arguments: Mapping[str, Any]) -> dict:
    return deepcopy({key: value for key, value in arguments.items() if key != "_sprout_context"})


_CALL: ContextVar[dict | None] = ContextVar("mcp_authorized_call", default=None)


@contextmanager
def _authorized_mcp_call(
    tool: MCPTool, arguments: Mapping[str, Any], identity: tuple[str, ...], binding: str,
):
    """Executor-only in-process handoff; never serialized as tool arguments.

    Trusted Python assembly is the boundary, not protection from arbitrary
    same-process Python introspection. A copied ContextVar in another Task is
    not a grant. All handoffs expire on exit, including cancellation/errors.
    """
    call = {"tool": tool, "binding": binding,
            "arguments": _canonical(_remote_arguments(arguments)), "identity": identity,
            "task": asyncio.current_task(), "used": False, "active": True}
    token = _CALL.set(call)
    try:
        yield
    finally:
        call["active"] = False
        _CALL.reset(token)


def _content_to_text(content: Any) -> str:
    """Extract readable text from an MCP CallToolResult content list."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    items = content if isinstance(content, (list, tuple)) else [content]
    for item in items:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text)
        elif item is not None:
            parts.append(str(item))
    return "\n".join(parts)


@dataclass(slots=True)
class MCPToolAdapter:
    """Converts external MCP tool descriptors into uniform ToolSpec objects."""

    default_risk_level: str = "medium"

    def to_tool_spec(self, remote_tool: Any, *, server_name: str = "") -> ToolSpec:
        name = remote_tool.name
        prefixed = f"{server_name}_{name}" if server_name else name
        schema = remote_tool.inputSchema or {}
        return ToolSpec(
            name=prefixed,
            description=remote_tool.description or f"MCP tool {name!r} from {server_name!r}",
            input_schema=deepcopy(dict(schema)) if isinstance(schema, Mapping) else {},
            risk_level=self.default_risk_level,
        )

    def to_tool(self, connection: MCPConnection, remote_tool: Any) -> MCPTool:
        return MCPTool(
            connection=connection,
            spec=self.to_tool_spec(remote_tool, server_name=connection.name),
            remote_name=remote_tool.name,
        )


@dataclass(slots=True)
class MCPTool:
    """A Tool implementation that proxies invoke() to a remote MCP server."""

    connection: MCPConnection
    spec: ToolSpec
    remote_name: str
    _manager: Any = field(default=None, init=False, repr=False, compare=False)

    async def invoke(self, arguments: Mapping[str, Any]) -> ToolResult:
        binding = _classification(self)
        if binding is None:
            return ToolResult.denied("MCP operation has no current trusted risk classification")
        call = _CALL.get()
        if (call is None or not call["active"] or call["used"] or call["tool"] is not self
                or call["binding"] != binding or call["task"] is not asyncio.current_task()
                or call["arguments"] != _canonical(_remote_arguments(arguments))):
            return ToolResult.denied("MCP operation requires a bound Executor authorization")
        call["used"] = True
        try:
            result = await self.connection.call_tool(self.remote_name, _remote_arguments(arguments))
        except asyncio.CancelledError as exc:
            exc.add_note("UNCONFIRMED MCP effects; do not automatically retry")
            self._manager._revoke_connection(self.connection)
            raise
        except Exception:
            # No transport receipt proves NOT_STARTED. Do not infer it from
            # exception messages, even for an apparent pre-send error.
            self._manager._revoke_connection(self.connection)
            raise ToolExecutionUnconfirmed("mcp_remote_execution_unconfirmed") from None
        is_error = bool(getattr(result, "isError", False))
        text = _content_to_text(getattr(result, "content", result))
        if is_error:
            return ToolResult.failure(text or "Remote MCP tool reported an error")
        return ToolResult.success(text, data={"server": self.connection.name})
