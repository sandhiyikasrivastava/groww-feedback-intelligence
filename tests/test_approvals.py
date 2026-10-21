"""Approval enforcement, rejection and duplicate-write prevention."""

from __future__ import annotations

from datetime import date

import pytest

from groww_intel.approvals import (
    ApprovalError,
    ApprovalStatus,
    ApprovalStore,
    DuplicateActionError,
    NotApprovedError,
    build_documentation_payload,
    content_hash,
    idempotency_key,
    render_documentation_markdown,
)
from groww_intel.schemas import ExecutionReceipt


@pytest.fixture()
def approval_store(tmp_path) -> ApprovalStore:
    return ApprovalStore(directory=tmp_path / "state")


def _request(store: ApprovalStore, content: str = "pulse body"):
    return store.create_request(
        action="documentation_append",
        destination="https://mcp.notion.com/mcp → page-1",
        content=content,
        summary="weekly pulse",
    )


def test_unapproved_action_cannot_be_executed(approval_store):
    request = _request(approval_store)
    with pytest.raises(NotApprovedError):
        approval_store.assert_executable(request, request.content)


def test_rejected_action_cannot_be_executed(approval_store):
    request = _request(approval_store)
    approval_store.reject(request.request_id, note="not now")
    rejected = approval_store.get(request.request_id)
    assert rejected.status == ApprovalStatus.REJECTED.value
    with pytest.raises(NotApprovedError):
        approval_store.assert_executable(rejected, rejected.content)


def test_rejected_action_cannot_be_approved_again(approval_store):
    request = _request(approval_store)
    approval_store.reject(request.request_id)
    with pytest.raises(ApprovalError):
        approval_store.approve(request.request_id)


def test_approved_action_can_be_executed(approval_store):
    request = _request(approval_store)
    approval_store.approve(request.request_id)
    approved = approval_store.get(request.request_id)
    approval_store.assert_executable(approved, approved.content)


def test_content_change_invalidates_the_approval(approval_store):
    request = _request(approval_store)
    approval_store.approve(request.request_id)
    approval_store.update_content(request.request_id, "a different body")
    updated = approval_store.get(request.request_id)
    assert updated.status == ApprovalStatus.INVALIDATED.value
    with pytest.raises(NotApprovedError):
        approval_store.assert_executable(updated, "a different body")


def test_execution_is_blocked_when_the_content_drifted(approval_store):
    request = _request(approval_store)
    approval_store.approve(request.request_id)
    approved = approval_store.get(request.request_id)
    with pytest.raises(NotApprovedError):
        approval_store.assert_executable(approved, "someone edited the text elsewhere")


def test_duplicate_write_is_prevented(approval_store):
    request = _request(approval_store)
    approval_store.approve(request.request_id)
    approved = approval_store.get(request.request_id)
    receipt = ExecutionReceipt(
        status="success", external_id="page-123", destination=approved.destination, detail="ok"
    )
    approval_store.record_execution(approved, receipt)

    # a fresh request for the same content
    retry = approval_store.create_request(
        action="documentation_append",
        destination=approved.destination,
        content=approved.content,
    )
    approval_store.approve(retry.request_id)
    retry_approved = approval_store.get(retry.request_id)
    with pytest.raises(DuplicateActionError):
        approval_store.assert_executable(retry_approved, retry_approved.content)


def test_audit_log_records_the_decisions(approval_store):
    request = _request(approval_store)
    approval_store.approve(request.request_id, decided_by="tester")
    approval_store.reject(
        approval_store.create_request(
            action="email_draft", destination="gmail", content="body"
        ).request_id,
        decided_by="tester",
    )
    entries = [
        line for line in approval_store.audit_path.read_text(encoding="utf-8").splitlines() if line
    ]
    events = [json_line.split('"event": "')[1].split('"')[0] for json_line in entries]
    assert "created" in events
    assert "approved" in events
    assert "rejected" in events


def test_content_hash_changes_with_the_content():
    assert content_hash("a") != content_hash("b")
    assert content_hash("a") == content_hash("a")
    assert idempotency_key("act", "dest", "a") != idempotency_key("act", "dest", "b")


def test_documentation_payload_has_the_required_fields():
    record = build_documentation_payload(
        pulse_body="pulse",
        fee_issue="brokerage",
        explanation_bullets=["one", "two"],
        source_links=["https://groww.in/pricing/"],
        top_themes=["Theme A"],
        review_date_range="2026-07-29 → 2026-10-19",
        reviews_analyzed=510,
        supporting_evidence=["r1", "r2"],
        entry_date=date(2026, 10, 21),
    )
    markdown = render_documentation_markdown(record)
    assert record.date == "2026-10-21"
    assert record.top_themes == ["Theme A"]
    assert record.weekly_pulse == "pulse"
    assert record.identified_fee_issue == "brokerage"
    assert record.explanation_bullets == ["one", "two"]
    assert record.source_links == ["https://groww.in/pricing/"]
    assert "2026-07-29" in markdown
    assert "510" in markdown
