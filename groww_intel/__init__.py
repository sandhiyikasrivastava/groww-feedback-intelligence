"""Groww Feedback Intelligence — analysis package.

Modules
-------
``ingestion``     CSV reading, column normalisation, de-duplication, window filter
``validation``    Pydantic schemas for every record that enters the pipeline
``analysis``      Theme discovery, ranking and verbatim quotation selection
``fee_issue``     Fee-confusion detection with a transparent evidence threshold
``sources``       Verification of fee facts against official Groww pages
``reports``       Weekly product pulse + customer-facing fee explanation
``approvals``     Human approval gate, audit log and duplicate-write prevention
``integrations``  MCP clients for Notion (documentation) and Gmail (drafts)
``llm``           Configurable LLM provider (Groq / OpenAI compatible)
``pipeline``      End-to-end orchestration used by the app, CLI and tests
"""

__all__ = [
    "analysis",
    "approvals",
    "config",
    "fee_issue",
    "ingestion",
    "integrations",
    "llm",
    "pipeline",
    "reports",
    "schemas",
    "sources",
]

__version__ = "1.0.0"
