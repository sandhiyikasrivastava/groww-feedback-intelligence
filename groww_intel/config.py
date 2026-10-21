"""Runtime configuration.

All secrets are read from the environment (local development) or from
Streamlit's secrets manager (deployment).  Nothing secret is ever written to
disk by this module and no default value contains a credential.

Precedence for secrets:
    1. ``st.secrets`` (Streamlit Community Cloud / deployment)
    2. environment variables (local development, CI)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
COLLECTED_DIR = DATA_DIR / "collected"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
SIMULATED_DIR = DATA_DIR / "simulated"
DOCS_DIR = PROJECT_ROOT / "docs"
STATE_DIR = DATA_DIR / "state"

#: Groww India App Store application id (the public Groww investing app).
GROWW_APPSTORE_APP_ID = "1404871703"
GROWW_APPSTORE_APP_NAME = "Groww Stocks, Mutual Fund, IPO"

#: Official Groww sources used for fee verification.  Every URL below was
#: fetched and checked live (see ``groww_intel/sources.py``); the help-centre
#: articles are the ones that actually resolve (no error page).
OFFICIAL_SOURCE_URLS = (
    "https://groww.in/pricing/",
    "https://groww.in/help/stocks/sx-pricing/what-is-the-charge-for-buy-order-delivery",
    "https://groww.in/help/stocks/sx-pricing/what-are-intraday-charges",
    "https://groww.in/help/stocks/sx-pricing/how-much-gst-is-charged",
    "https://groww.in/calculators/brokerage-calculator/",
)


def _streamlit_secrets() -> Mapping[str, Any]:
    """Return Streamlit secrets if we are running inside Streamlit."""
    try:  # pragma: no cover - only meaningful inside Streamlit runtime
        import streamlit as st

        if hasattr(st, "secrets"):
            return st.secrets
    except Exception:
        pass
    return {}


def _secret(name: str) -> Optional[str]:
    secrets = _streamlit_secrets()
    value: Any = None
    try:
        value = secrets.get(name)  # type: ignore[union-attr]
    except Exception:
        value = None
    if not value:
        value = os.getenv(name)
    if not value:
        return None
    value = str(value).strip()
    return value or None


@dataclass
class LLMConfig:
    """Configurable LLM provider (OpenAI-compatible chat completions)."""

    provider: str = "none"  # "none" | "groq" | "openai"
    api_key: Optional[str] = None
    model: str = ""
    base_url: str = ""
    temperature: float = 0.0
    max_tokens: int = 1400
    timeout_seconds: float = 60.0

    @property
    def available(self) -> bool:
        return self.provider != "none" and bool(self.api_key) and bool(self.model)

    def describe(self) -> str:
        if not self.available:
            return "Offline deterministic mode (no LLM credentials configured)"
        return f"{self.provider} / {self.model}"


def load_llm_config() -> LLMConfig:
    """Resolve the LLM provider from environment variables or Streamlit secrets."""
    provider = (_secret("LLM_PROVIDER") or "").lower()
    groq_key = _secret("GROQ_API_KEY")
    openai_key = _secret("OPENAI_API_KEY")

    if not provider:
        if groq_key:
            provider = "groq"
        elif openai_key:
            provider = "openai"
        else:
            provider = "none"

    if provider == "groq":
        return LLMConfig(
            provider="groq",
            api_key=groq_key,
            model=_secret("GROQ_MODEL") or "llama-3.3-70b-versatile",
            base_url=_secret("GROQ_BASE_URL") or "https://api.groq.com/openai/v1",
        )
    if provider == "openai":
        return LLMConfig(
            provider="openai",
            api_key=openai_key,
            model=_secret("OPENAI_MODEL") or "gpt-4o-mini",
            base_url=_secret("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        )
    return LLMConfig(provider="none")


@dataclass
class MCPConfig:
    """Configuration for the MCP-based external integrations."""

    # Notion (documentation service) — official remote MCP server.
    notion_mcp_url: str = "https://mcp.notion.com/mcp"
    notion_api_key: Optional[str] = None
    notion_parent_page_id: Optional[str] = None
    notion_database_id: Optional[str] = None

    # Gmail (email drafts) — official MCP server over stdio (npx).
    gmail_command: str = "npx"
    gmail_args: str = "-y @modelcontextprotocol/server-gmail"
    gmail_oauth_credentials: Optional[str] = None
    gmail_email_address: Optional[str] = None

    def any_configured(self) -> bool:
        return bool(self.notion_api_key) or bool(self.gmail_oauth_credentials)


def load_mcp_config() -> MCPConfig:
    return MCPConfig(
        notion_mcp_url=_secret("NOTION_MCP_URL") or "https://mcp.notion.com/mcp",
        notion_api_key=_secret("NOTION_API_KEY") or _secret("NOTION_TOKEN"),
        notion_parent_page_id=_secret("NOTION_PARENT_PAGE_ID"),
        notion_database_id=_secret("NOTION_DATABASE_ID"),
        gmail_command=_secret("GMAIL_MCP_COMMAND") or "npx",
        gmail_args=_secret("GMAIL_MCP_ARGS") or "-y @modelcontextprotocol/server-gmail",
        gmail_oauth_credentials=_secret("GMAIL_OAUTH_CREDENTIALS"),
        gmail_email_address=_secret("GMAIL_EMAIL_ADDRESS"),
    )


@dataclass
class AppSettings:
    """Application-wide settings and analysis thresholds."""

    weeks_min: int = 8
    weeks_max: int = 12
    default_weeks: int = 12
    max_themes: int = 5
    max_quotations: int = 3
    pulse_max_words: int = 250
    explanation_max_bullets: int = 6
    recommendations_count: int = 3

    # Fee-issue evidence threshold (transparent, see fee_issue.py).
    fee_min_supporting_reviews: int = 3
    fee_min_distinct_weeks: int = 2
    fee_min_explicit_mentions: int = 2
    fee_max_single_area_share: float = 0.7

    # Source freshness window for snapshots (days).
    snapshot_max_age_days: int = 30

    llm: LLMConfig = field(default_factory=load_llm_config)
    mcp: MCPConfig = field(default_factory=load_mcp_config)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()


def ensure_dirs() -> None:
    """Create the local state/output directories (idempotent)."""
    for path in (DATA_DIR, COLLECTED_DIR, SNAPSHOT_DIR, SIMULATED_DIR, DOCS_DIR, STATE_DIR):
        path.mkdir(parents=True, exist_ok=True)
