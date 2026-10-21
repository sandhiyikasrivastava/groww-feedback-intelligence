"""A thin, synchronous wrapper around the official MCP Python SDK.

Two transports are supported:

* **stdio** — used by the reference MCP servers that ship as npm packages
  (for example ``@modelcontextprotocol/server-gmail``).
* **streamable HTTP** — used by remote MCP servers such as the official
  Notion server at ``https://mcp.notion.com/mcp``.

The wrapper is deliberately small: it connects, lists the tools the server
exposes, calls exactly one tool and returns the structured result.  Callers
must pass an explicit tool name — the client never guesses a write tool.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .base import IntegrationError


@dataclass
class McpToolCall:
    """The outcome of one MCP tool call."""

    tool_name: str
    is_error: bool
    content: list[Any] = field(default_factory=list)
    structured: Optional[dict] = None
    raw: dict = field(default_factory=dict)

    def as_text(self) -> str:
        parts: list[str] = []
        for item in self.content:
            text = getattr(item, "text", None)
            if text is None:
                text = str(item)
            parts.append(text)
        if not parts and self.structured:
            parts.append(json.dumps(self.structured, ensure_ascii=False))
        return "\n".join(parts)


@dataclass
class McpServerSpec:
    """How to reach an MCP server."""

    transport: str  # "stdio" | "http"
    command: Optional[str] = None
    args: Sequence[str] = ()
    env: dict = field(default_factory=dict)
    url: Optional[str] = None
    headers: dict = field(default_factory=dict)


def call_tool(
    spec: McpServerSpec,
    tool_name: str,
    arguments: dict,
    *,
    timeout: float = 60.0,
) -> McpToolCall:
    """Call one tool on an MCP server and return its result."""
    try:
        return asyncio.run(_call_tool_async(spec, tool_name, arguments, timeout=timeout))
    except IntegrationError:
        raise
    except Exception as exc:  # pragma: no cover - depends on the server
        raise IntegrationError(f"MCP call to '{tool_name}' failed: {exc}") from exc


def list_tools(spec: McpServerSpec, *, timeout: float = 30.0) -> list[str]:
    """Return the tool names exposed by the server."""
    try:
        return asyncio.run(_list_tools_async(spec, timeout=timeout))
    except IntegrationError:
        raise
    except Exception as exc:  # pragma: no cover - depends on the server
        raise IntegrationError(f"Could not list MCP tools: {exc}") from exc


async def _list_tools_async(spec: McpServerSpec, *, timeout: float) -> list[str]:
    async with _session(spec) as session:
        await session.initialize()
        result = await asyncio.wait_for(session.list_tools(), timeout=timeout)
        return [tool.name for tool in result.tools]


async def _call_tool_async(
    spec: McpServerSpec, tool_name: str, arguments: dict, *, timeout: float
) -> McpToolCall:
    async with _session(spec) as session:
        await session.initialize()
        result = await asyncio.wait_for(
            session.call_tool(tool_name, arguments), timeout=timeout
        )
        structured = getattr(result, "structuredContent", None)
        if structured is None:
            structured = getattr(result, "structured_content", None)
        return McpToolCall(
            tool_name=tool_name,
            is_error=bool(getattr(result, "isError", False)),
            content=list(getattr(result, "content", []) or []),
            structured=structured if isinstance(structured, dict) else None,
            raw={"isError": bool(getattr(result, "isError", False))},
        )


def _session(spec: McpServerSpec):
    """Return an async context manager yielding an initialised ClientSession."""
    from contextlib import asynccontextmanager

    from mcp import ClientSession

    @asynccontextmanager
    async def _open():
        if spec.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=spec.command or "",
                args=list(spec.args),
                env=dict(spec.env) if spec.env else None,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    yield session
        elif spec.transport == "http":
            from mcp.client.streamable_http import streamable_http_client

            async with streamable_http_client(spec.url or "", headers=spec.headers or None) as (
                read,
                write,
                _get_session_id,
            ):
                async with ClientSession(read, write) as session:
                    yield session
        else:
            raise IntegrationError(f"Unsupported MCP transport: {spec.transport!r}")

    return _open()
