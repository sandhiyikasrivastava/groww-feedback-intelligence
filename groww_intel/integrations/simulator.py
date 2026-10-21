"""Explicitly labelled simulation of the external services.

Used when the live credentials for Notion or Gmail are not available.  The
payloads are written to local JSONL files so the run can be inspected, and
every receipt carries ``status="simulated"`` and ``simulated=True`` so the UI
can never present a simulation as a real write.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import SIMULATED_DIR
from ..schemas import DocRecord, ExecutionReceipt


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


class SimulatedDocumentationAppender:
    """Writes the documentation record to a local file instead of Notion."""

    name = "Simulated documentation service (local file)"
    simulated = True

    def __init__(self, directory: Optional[Path] = None, reason: str = "") -> None:
        self.directory = Path(directory or SIMULATED_DIR)
        self.path = self.directory / "documentation_entries.jsonl"
        self.reason = reason

    def append(self, record: DocRecord, markdown: str) -> ExecutionReceipt:
        entry = {
            "simulated": True,
            "reason": self.reason,
            "appended_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "record": record.model_dump(mode="json"),
            "markdown": markdown,
        }
        _append_jsonl(self.path, entry)
        return ExecutionReceipt(
            status="simulated",
            external_id=f"simulated-doc-{uuid.uuid4().hex[:10]}",
            destination=str(self.path),
            detail=(
                "SIMULATION: the record was appended to a local file instead of a live "
                "documentation service."
                + (f" Reason: {self.reason}" if self.reason else "")
            ),
            simulated=True,
            raw_response={"simulated": True},
        )


class SimulatedEmailDrafter:
    """Writes the email draft to a local file instead of Gmail."""

    name = "Simulated Gmail draft service (local file)"
    simulated = True

    def __init__(self, directory: Optional[Path] = None, reason: str = "") -> None:
        self.directory = Path(directory or SIMULATED_DIR)
        self.path = self.directory / "email_drafts.jsonl"
        self.reason = reason

    def create_draft(self, subject: str, body: str, recipient: Optional[str] = None) -> ExecutionReceipt:
        entry = {
            "simulated": True,
            "reason": self.reason,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "subject": subject,
            "recipient": recipient or "",
            "body": body,
            "sent": False,
        }
        _append_jsonl(self.path, entry)
        return ExecutionReceipt(
            status="simulated",
            external_id=f"simulated-draft-{uuid.uuid4().hex[:10]}",
            destination=str(self.path),
            detail=(
                "SIMULATION: the draft was written to a local file instead of Gmail. "
                "Nothing was sent."
                + (f" Reason: {self.reason}" if self.reason else "")
            ),
            simulated=True,
            raw_response={"simulated": True, "recipient": recipient or ""},
        )
