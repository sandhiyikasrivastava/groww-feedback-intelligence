"""Create an email **draft** through MCP (never send).

The live path uses the official reference MCP server for Gmail
(``@modelcontextprotocol/server-gmail``) over stdio and calls exactly one
tool: ``create_draft``.

Safety rules that are enforced in code:

* the module never references the server's ``send_email`` tool — only
  ``create_draft`` is allowed (see :data:`ALLOWED_TOOLS`);
* the recipient may be empty — an empty recipient is passed through as an
  empty string rather than a made-up address;
* nothing is sent: the server is asked for a draft only.
"""

from __future__ import annotations

import os
import uuid
from typing import Optional

from ..config import MCPConfig
from ..schemas import ExecutionReceipt
from .base import IntegrationError, MissingCredentialsError
from .mcp_client import McpServerSpec, call_tool

#: The only Gmail tool this integration is allowed to call.
ALLOWED_TOOLS = ("create_draft",)

#: Tools that must never be called by this application.
FORBIDDEN_TOOLS = ("send_email", "send_mail", "sendMessage")


class GmailDraftCreator:
    """Creates a Gmail draft through the official Gmail MCP server."""

    name = "Gmail (official @modelcontextprotocol/server-gmail)"
    simulated = False

    def __init__(self, config: MCPConfig) -> None:
        self.config = config
        if not config.gmail_oauth_credentials:
            raise MissingCredentialsError(
                "GMAIL_OAUTH_CREDENTIALS is not set — the Gmail MCP server cannot authenticate."
            )

    def _spec(self) -> McpServerSpec:
        env = dict(os.environ)
        credentials = self.config.gmail_oauth_credentials or ""
        env["GOOGLE_OAUTH_CREDENTIALS_PATH"] = credentials
        env["GMAIL_OAUTH_CREDENTIALS_PATH"] = credentials
        if self.config.gmail_email_address:
            env["GMAIL_EMAIL"] = self.config.gmail_email_address
        args = [part for part in (self.config.gmail_args or "").split() if part]
        return McpServerSpec(
            transport="stdio",
            command=self.config.gmail_command,
            args=args,
            env=env,
        )

    def create_draft(self, subject: str, body: str, recipient: Optional[str] = None) -> ExecutionReceipt:
        tool_name = ALLOWED_TOOLS[0]
        if tool_name in FORBIDDEN_TOOLS:  # pragma: no cover - constant guard
            raise IntegrationError("Refusing to call a sending tool.")
        arguments = {
            "to": recipient or "",
            "subject": subject,
            "body": body,
        }
        result = call_tool(self._spec(), tool_name, arguments)
        if result.is_error:
            return ExecutionReceipt(
                status="failed",
                destination="Gmail (MCP)",
                detail=f"The Gmail MCP server returned an error: {result.as_text()[:400]}",
                simulated=False,
                raw_response={"tool": tool_name},
            )
        external_id = None
        if result.structured:
            for key in ("id", "draft_id", "messageId", "draftId"):
                if key in result.structured:
                    external_id = str(result.structured[key])
                    break
        return ExecutionReceipt(
            status="success",
            external_id=external_id or f"gmail-draft-{uuid.uuid4().hex[:10]}",
            destination="Gmail (MCP)",
            detail=(
                "Draft created with the Gmail MCP tool 'create_draft'. "
                "The message was NOT sent."
                + ("" if recipient else " No recipient was set (left blank).")
            ),
            simulated=False,
            raw_response={"tool": tool_name, "recipient": recipient or ""},
        )
