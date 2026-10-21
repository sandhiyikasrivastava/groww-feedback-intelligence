"""External integrations: MCP wiring, simulations, failures, no auto-send."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from groww_intel.config import MCPConfig
from groww_intel.integrations import (
    get_documentation_appender,
    get_email_drafter,
)
from groww_intel.integrations.base import IntegrationError, MissingCredentialsError
from groww_intel.integrations.gmail_draft import (
    ALLOWED_TOOLS,
    FORBIDDEN_TOOLS,
    GmailDraftCreator,
)
from groww_intel.integrations.mcp_client import McpServerSpec, call_tool, list_tools
from groww_intel.integrations.notion_docs import NotionDocumentationAppender
from groww_intel.integrations.simulator import (
    SimulatedDocumentationAppender,
    SimulatedEmailDrafter,
)
from groww_intel.schemas import DocRecord, ExecutionReceipt

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _record() -> DocRecord:
    return DocRecord(
        date="2026-10-21",
        top_themes=["Charges"],
        weekly_pulse="pulse body",
        identified_fee_issue="brokerage",
        explanation_bullets=["one"],
        source_links=["https://groww.in/pricing/"],
    )


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------


def test_simulation_is_labelled_and_writes_the_payload(tmp_path):
    service = SimulatedDocumentationAppender(directory=tmp_path, reason="no key")
    receipt = service.append(_record(), "# markdown")
    assert receipt.status == "simulated"
    assert receipt.simulated is True
    assert "SIMULATION" in receipt.detail
    written = json.loads((tmp_path / "documentation_entries.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert written["simulated"] is True
    assert written["record"]["identified_fee_issue"] == "brokerage"


def test_simulated_email_draft_is_not_sent(tmp_path):
    service = SimulatedEmailDrafter(directory=tmp_path, reason="no key")
    receipt = service.create_draft("Subject", "Body", recipient=None)
    assert receipt.status == "simulated"
    assert receipt.simulated is True
    payload = json.loads((tmp_path / "email_drafts.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert payload["sent"] is False
    assert payload["recipient"] == ""


def test_factory_falls_back_to_simulation_without_credentials():
    doc = get_documentation_appender(MCPConfig())
    mail = get_email_drafter(MCPConfig())
    assert doc.simulated is True
    assert mail.simulated is True
    assert "NOTION_API_KEY" in doc.reason
    assert "GMAIL_OAUTH_CREDENTIALS" in mail.reason


def test_live_services_require_credentials():
    with pytest.raises(MissingCredentialsError):
        NotionDocumentationAppender(MCPConfig())
    with pytest.raises(MissingCredentialsError):
        GmailDraftCreator(MCPConfig())


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------


def test_failed_integration_returns_a_failed_receipt(tmp_path, monkeypatch):
    service = SimulatedDocumentationAppender(directory=tmp_path)
    monkeypatch.setattr(
        service,
        "append",
        lambda record, markdown: ExecutionReceipt(
            status="failed", destination="x", detail="service is down", simulated=True
        ),
    )
    receipt = service.append(_record(), "# markdown")
    assert receipt.status == "failed"


def test_mcp_call_error_is_wrapped():
    spec = McpServerSpec(transport="stdio", command="definitely-not-a-real-binary", args=[])
    with pytest.raises(IntegrationError):
        call_tool(spec, "create_draft", {"to": "", "subject": "s", "body": "b"})


# --------------------------------------------------------------------------
# The MCP transport itself (against a local MCP server)
# --------------------------------------------------------------------------

SERVER_SCRIPT = textwrap.dedent(
    """
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("test-server")

    @server.tool()
    def create_draft(to: str, subject: str, body: str) -> str:
        \"\"\"Create a draft.\"\"\"
        return f"draft:{to}:{subject}:{len(body)}"

    if __name__ == "__main__":
        server.run("stdio")
    """
)


def test_mcp_client_talks_to_a_real_mcp_server(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(SERVER_SCRIPT, encoding="utf-8")
    spec = McpServerSpec(
        transport="stdio",
        command="python",
        args=[str(script)],
        env=None,
    )
    tools = list_tools(spec)
    assert "create_draft" in tools

    result = call_tool(spec, "create_draft", {"to": "", "subject": "hello", "body": "world"})
    assert result.is_error is False
    assert "draft::hello:5" in result.as_text()


# --------------------------------------------------------------------------
# Safety: no automatic sending anywhere in the code base
# --------------------------------------------------------------------------


def test_gmail_module_only_uses_the_draft_tool():
    assert ALLOWED_TOOLS == ("create_draft",)
    assert "send_email" in FORBIDDEN_TOOLS


def test_no_module_calls_a_send_tool():
    offenders = []
    for path in (PROJECT_ROOT / "groww_intel").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "send_email(" in stripped or "sendEmail(" in stripped or "send_mail(" in stripped:
                offenders.append(f"{path.name}: {stripped}")
    assert offenders == []


def test_email_creation_never_sends(tmp_path):
    service = SimulatedEmailDrafter(directory=tmp_path)
    receipt = service.create_draft("s", "b")
    assert receipt.raw_response is not None
    assert receipt.raw_response.get("simulated") is True
