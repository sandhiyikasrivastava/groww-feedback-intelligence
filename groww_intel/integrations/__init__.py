"""Factory that picks the live integration or the labelled simulation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..config import MCPConfig, load_mcp_config
from .base import (
    DocumentationAppender,
    EmailDrafter,
    IntegrationError,
    MissingCredentialsError,
)
from .gmail_draft import GmailDraftCreator
from .notion_docs import NotionDocumentationAppender
from .simulator import SimulatedDocumentationAppender, SimulatedEmailDrafter


@dataclass
class IntegrationChoice:
    """The chosen integration plus the reason it was chosen."""

    service: object
    simulated: bool
    reason: str

    @property
    def name(self) -> str:
        return getattr(self.service, "name", type(self.service).__name__)


def get_documentation_appender(config: Optional[MCPConfig] = None) -> IntegrationChoice:
    """Live Notion MCP server when configured, otherwise the simulation."""
    config = config or load_mcp_config()
    try:
        service = NotionDocumentationAppender(config)
        return IntegrationChoice(
            service=service,
            simulated=False,
            reason="Live Notion MCP server configured (NOTION_API_KEY + parent page id).",
        )
    except MissingCredentialsError as exc:
        return IntegrationChoice(
            service=SimulatedDocumentationAppender(reason=str(exc)),
            simulated=True,
            reason=str(exc),
        )


def get_email_drafter(config: Optional[MCPConfig] = None) -> IntegrationChoice:
    """Live Gmail MCP server when configured, otherwise the simulation."""
    config = config or load_mcp_config()
    try:
        service = GmailDraftCreator(config)
        return IntegrationChoice(
            service=service,
            simulated=False,
            reason="Live Gmail MCP server configured (GMAIL_OAUTH_CREDENTIALS).",
        )
    except MissingCredentialsError as exc:
        return IntegrationChoice(
            service=SimulatedEmailDrafter(reason=str(exc)),
            simulated=True,
            reason=str(exc),
        )


__all__ = [
    "DocumentationAppender",
    "EmailDrafter",
    "IntegrationChoice",
    "IntegrationError",
    "MissingCredentialsError",
    "get_documentation_appender",
    "get_email_drafter",
]
