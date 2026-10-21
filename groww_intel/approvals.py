"""Approval workflow for external write actions.

Rules enforced here:

* an external action can only run against content that was **approved**;
* if the content changes after approval the approval is invalidated and the
  action must be approved again;
* a rejected action can never be executed;
* every decision is written to an append-only audit log;
* an idempotency key derived from (action, destination, content) prevents a
  duplicate write when a user retries.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .config import STATE_DIR, ensure_dirs
from .schemas import DocRecord, ExecutionReceipt


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
    INVALIDATED = "invalidated"


class ApprovalError(Exception):
    """Base error for approval violations."""


class NotApprovedError(ApprovalError):
    """Raised when an action is executed without a valid approval."""


class DuplicateActionError(ApprovalError):
    """Raised when the same write is attempted twice."""


@dataclass
class ApprovalRequest:
    """One proposed external action awaiting a human decision."""

    request_id: str
    action: str  # "documentation_append" | "email_draft"
    destination: str
    content: str  # exactly what will be written / drafted
    content_hash: str
    summary: str = ""
    details: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    status: str = ApprovalStatus.PENDING.value
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None
    decision_note: Optional[str] = None
    execution_result: Optional[dict] = None
    idempotency_key: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ApprovalRequest":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


def content_hash(content: str) -> str:
    """Stable hash of the exact content that will be written."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def idempotency_key(action: str, destination: str, content: str) -> str:
    """Key identifying a unique write (same action + destination + content)."""
    payload = f"{action}|{destination}|{content_hash(content)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ApprovalStore:
    """File-backed store for approval requests and the audit log."""

    def __init__(self, directory: Optional[Path] = None) -> None:
        ensure_dirs()
        self.directory = Path(directory or STATE_DIR)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.requests_path = self.directory / "approval_requests.json"
        self.audit_path = self.directory / "audit_log.jsonl"
        self.ledger_path = self.directory / "execution_ledger.json"

    # -- persistence -----------------------------------------------------
    def _load(self) -> dict[str, ApprovalRequest]:
        if not self.requests_path.exists():
            return {}
        try:
            payload = json.loads(self.requests_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return {
            item["request_id"]: ApprovalRequest.from_dict(item)
            for item in payload.get("requests", [])
        }

    def _save(self, requests: dict[str, ApprovalRequest]) -> None:
        self.requests_path.write_text(
            json.dumps(
                {"requests": [r.to_dict() for r in requests.values()]},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _audit(self, event: str, request: ApprovalRequest, extra: Optional[dict] = None) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "request_id": request.request_id,
            "action": request.action,
            "destination": request.destination,
            "content_hash": request.content_hash,
            "status": request.status,
            "decided_by": request.decided_by,
        }
        if extra:
            entry.update(extra)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- API -------------------------------------------------------------
    def create_request(
        self,
        *,
        action: str,
        destination: str,
        content: str,
        summary: str = "",
        details: Optional[dict] = None,
    ) -> ApprovalRequest:
        requests = self._load()
        digest = content_hash(content)
        key = idempotency_key(action, destination, content)
        request_id = f"{action}-{digest[:12]}"
        request = ApprovalRequest(
            request_id=request_id,
            action=action,
            destination=destination,
            content=content,
            content_hash=digest,
            summary=summary,
            details=details or {},
            idempotency_key=key,
        )
        requests[request_id] = request
        self._save(requests)
        self._audit("created", request)
        return request

    def get(self, request_id: str) -> Optional[ApprovalRequest]:
        return self._load().get(request_id)

    def list_requests(self) -> list[ApprovalRequest]:
        return sorted(self._load().values(), key=lambda r: r.created_at, reverse=True)

    def approve(self, request_id: str, decided_by: str = "operator", note: str = "") -> ApprovalRequest:
        requests = self._load()
        request = requests.get(request_id)
        if request is None:
            raise ApprovalError(f"Unknown approval request: {request_id}")
        if request.status == ApprovalStatus.REJECTED.value:
            raise ApprovalError("A rejected action cannot be approved.")
        request.status = ApprovalStatus.APPROVED.value
        request.decided_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        request.decided_by = decided_by
        request.decision_note = note
        requests[request_id] = request
        self._save(requests)
        self._audit("approved", request, {"note": note})
        return request

    def reject(self, request_id: str, decided_by: str = "operator", note: str = "") -> ApprovalRequest:
        requests = self._load()
        request = requests.get(request_id)
        if request is None:
            raise ApprovalError(f"Unknown approval request: {request_id}")
        request.status = ApprovalStatus.REJECTED.value
        request.decided_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        request.decided_by = decided_by
        request.decision_note = note
        requests[request_id] = request
        self._save(requests)
        self._audit("rejected", request, {"note": note})
        return request

    def update_content(self, request_id: str, content: str) -> ApprovalRequest:
        """Change the content of a request — invalidates a previous approval."""
        requests = self._load()
        request = requests.get(request_id)
        if request is None:
            raise ApprovalError(f"Unknown approval request: {request_id}")
        if content == request.content:
            return request
        request.content = content
        request.content_hash = content_hash(content)
        request.idempotency_key = idempotency_key(request.action, request.destination, content)
        if request.status == ApprovalStatus.APPROVED.value:
            request.status = ApprovalStatus.INVALIDATED.value
        requests[request_id] = request
        self._save(requests)
        self._audit("content_changed", request)
        return request

    # -- execution -------------------------------------------------------
    def _ledger(self) -> dict:
        if not self.ledger_path.exists():
            return {}
        try:
            return json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_ledger(self, ledger: dict) -> None:
        self.ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")

    def executed_before(self, request: ApprovalRequest) -> Optional[dict]:
        return self._ledger().get(request.idempotency_key)

    def record_execution(self, request: ApprovalRequest, receipt: ExecutionReceipt) -> None:
        ledger = self._ledger()
        ledger[request.idempotency_key] = {
            "request_id": request.request_id,
            "action": request.action,
            "destination": request.destination,
            "content_hash": request.content_hash,
            "executed_at": receipt.executed_at.isoformat(),
            "status": receipt.status,
            "external_id": receipt.external_id,
            "simulated": receipt.simulated,
        }
        self._save_ledger(ledger)

        requests = self._load()
        stored = requests.get(request.request_id)
        if stored is not None:
            stored.status = (
                ApprovalStatus.EXECUTED.value if receipt.status in {"success", "simulated"} else ApprovalStatus.FAILED.value
            )
            stored.execution_result = receipt.model_dump(mode="json")
            requests[request.request_id] = stored
            self._save(requests)
        self._audit(
            "executed",
            request,
            {"status": receipt.status, "external_id": receipt.external_id, "simulated": receipt.simulated},
        )

    def assert_executable(self, request: ApprovalRequest, current_content: str) -> None:
        """Raise unless the request may run right now."""
        if request.status == ApprovalStatus.REJECTED.value:
            raise NotApprovedError(
                "This action was rejected by the reviewer; it cannot be executed."
            )
        if request.status != ApprovalStatus.APPROVED.value:
            raise NotApprovedError(
                f"This action is '{request.status}' — an explicit human approval is required before it can run."
            )
        if content_hash(current_content) != request.content_hash:
            raise NotApprovedError(
                "The content changed after it was approved. The approval was invalidated — "
                "review the updated content and approve it again."
            )
        previous = self.executed_before(request)
        if previous is not None:
            raise DuplicateActionError(
                "This exact content was already written to "
                f"{request.destination} on {previous.get('executed_at')} "
                f"(external id: {previous.get('external_id')}). The write was not repeated."
            )


# --------------------------------------------------------------------------
# Payload builders
# --------------------------------------------------------------------------


def build_documentation_payload(
    *,
    pulse_body: str,
    fee_issue: str,
    explanation_bullets: list[str],
    source_links: list[str],
    top_themes: list[str],
    review_date_range: str,
    reviews_analyzed: int,
    supporting_evidence: list[str],
    entry_date: Optional[date] = None,
) -> DocRecord:
    """The structured record appended to the documentation service."""
    return DocRecord(
        date=(entry_date or date.today()).isoformat(),
        top_themes=top_themes,
        weekly_pulse=pulse_body,
        identified_fee_issue=fee_issue,
        explanation_bullets=explanation_bullets,
        source_links=source_links,
        review_date_range=review_date_range,
        reviews_analyzed=reviews_analyzed,
        supporting_evidence=supporting_evidence,
        execution_status="pending",
    )


def render_documentation_markdown(record: DocRecord) -> str:
    """Human-readable rendering of the documentation record (also used as the body)."""
    lines = [
        f"## Weekly Product Pulse + Customer Clarification — Groww ({record.date})",
        "",
        f"**Review window:** {record.review_date_range} · **Reviews analysed:** {record.reviews_analyzed}",
        "",
        "### Top themes",
    ]
    lines += [f"- {theme}" for theme in record.top_themes] or ["- (none)"]
    lines += ["", "### Weekly pulse", "", record.weekly_pulse]
    lines += ["", "### Identified fee issue", "", record.identified_fee_issue]
    lines += ["", "### Customer-facing fee explanation", ""]
    lines += [f"- {bullet}" for bullet in record.explanation_bullets] or ["- (none)"]
    lines += ["", "### Verified official sources"]
    lines += [f"- {link}" for link in record.source_links] or ["- (none)"]
    if record.supporting_evidence:
        lines += ["", "### Supporting evidence (review ids)"]
        lines += [f"- {item}" for item in record.supporting_evidence]
    lines += ["", f"_Execution status: {record.execution_status}_"]
    return "\n".join(lines)
