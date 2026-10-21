"""Append the weekly record to a documentation service through MCP.

Live mode uses the **official Notion MCP server**
(``https://mcp.notion.com/mcp``) and appends a child page to the configured
parent page, so existing notes are never overwritten.

If the Notion credentials are not configured the app falls back to a clearly
labelled *simulated* service that writes the same payload to a local file —
the receipt then says ``simulated`` and the UI shows a simulation banner.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from ..config import MCPConfig
from ..schemas import DocRecord, ExecutionReceipt
from .base import IntegrationError, MissingCredentialsError
from .mcp_client import McpServerSpec, call_tool, list_tools

#: Tool names exposed by the official Notion MCP server that create a page.
NOTION_PAGE_TOOLS = (
    "mcp-notion-post-page",
    "API-post-page",
    "post-page",
    "create-pages",
    "notion-create-page",
)


class NotionDocumentationAppender:
    """Appends a structured record to Notion via the official MCP server."""

    name = "Notion (official MCP server)"
    simulated = False

    def __init__(self, config: MCPConfig) -> None:
        self.config = config
        if not config.notion_api_key:
            raise MissingCredentialsError(
                "NOTION_API_KEY is not set — the live Notion MCP server cannot be used."
            )
        if not config.notion_parent_page_id and not config.notion_database_id:
            raise MissingCredentialsError(
                "NOTION_PARENT_PAGE_ID (or NOTION_DATABASE_ID) is not set — "
                "there is no destination to append to."
            )

    # -- MCP plumbing ----------------------------------------------------
    def _spec(self) -> McpServerSpec:
        return McpServerSpec(
            transport="http",
            url=self.config.notion_mcp_url,
            headers={
                "Authorization": f"Bearer {self.config.notion_api_key}",
                "Notion-Version": "2022-06-28",
            },
        )

    def _resolve_tool(self) -> str:
        try:
            available = list_tools(self._spec())
        except IntegrationError:
            available = []
        for candidate in NOTION_PAGE_TOOLS:
            if candidate in available:
                return candidate
        for candidate in available:
            if "page" in candidate.lower() and "post" in candidate.lower():
                return candidate
        raise IntegrationError(
            "The Notion MCP server did not expose a page-creation tool "
            f"(available tools: {', '.join(available) or 'none'})."
        )

    def _arguments(self, record: DocRecord, markdown: str) -> dict:
        title = f"Weekly Product Pulse + Customer Clarification — Groww ({record.date})"
        if self.config.notion_database_id:
            return {
                "parent": {"database_id": self.config.notion_database_id},
                "properties": {
                    "Name": {"title": [{"text": {"content": title}}]},
                    "Date": {"date": {"start": record.date}},
                },
                "children": _markdown_to_blocks(markdown),
            }
        return {
            "parent": {"page_id": self.config.notion_parent_page_id},
            "properties": {"title": [{"text": {"content": title}}]},
            "children": _markdown_to_blocks(markdown),
        }

    # -- public API ------------------------------------------------------
    def append(self, record: DocRecord, markdown: str) -> ExecutionReceipt:
        spec = self._spec()
        tool_name = self._resolve_tool()
        result = call_tool(spec, tool_name, self._arguments(record, markdown))
        if result.is_error:
            return ExecutionReceipt(
                status="failed",
                destination=f"{self.config.notion_mcp_url} → {self.config.notion_parent_page_id or self.config.notion_database_id}",
                detail=f"The Notion MCP server returned an error: {result.as_text()[:400]}",
                simulated=False,
                raw_response={"tool": tool_name},
            )
        external_id = None
        if result.structured:
            for key in ("id", "page_id", "pageId"):
                if key in result.structured:
                    external_id = str(result.structured[key])
                    break
        return ExecutionReceipt(
            status="success",
            external_id=external_id or f"notion-{uuid.uuid4().hex[:10]}",
            destination=f"{self.config.notion_mcp_url} → {self.config.notion_parent_page_id or self.config.notion_database_id}",
            detail=f"Page created with the Notion MCP tool '{tool_name}'.",
            simulated=False,
            raw_response={"tool": tool_name},
        )


def _markdown_to_blocks(markdown: str) -> list[dict]:
    """Convert the markdown record into Notion block children."""
    blocks: list[dict] = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("### "):
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:]}}]},
                }
            )
        elif line.startswith("## "):
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]},
                }
            )
        elif line.startswith("- "):
            blocks.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": line[2:]}}]
                    },
                }
            )
        elif line.startswith("> "):
            blocks.append(
                {
                    "object": "block",
                    "type": "quote",
                    "quote": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]},
                }
            )
        else:
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": line[:1900]}}]},
                }
            )
    return blocks[:100]
