"""Groww Feedback Intelligence — Streamlit application.

Run with:
    streamlit run app.py

The app walks through: CSV upload → data-quality validation → theme
inspection → fee-issue verification → source review → report editing →
human approval → external writes (documentation append + email draft).
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from groww_intel.approvals import (  # noqa: E402
    ApprovalError,
    ApprovalStore,
    build_documentation_payload,
    render_documentation_markdown,
)
from groww_intel.config import (  # noqa: E402
    COLLECTED_DIR,
    DOCS_DIR,
    OFFICIAL_SOURCE_URLS,
    PROJECT_ROOT as CONFIG_ROOT,
    ensure_dirs,
    get_settings,
)
from groww_intel.integrations import (  # noqa: E402
    get_documentation_appender,
    get_email_drafter,
)
from groww_intel.ingestion import IngestionError  # noqa: E402
from groww_intel.llm import LLMClient  # noqa: E402
from groww_intel.pipeline import PipelineResult, run_pipeline  # noqa: E402
from groww_intel.reports import count_words  # noqa: E402
from groww_intel.schemas import ExecutionReceipt  # noqa: E402

st.set_page_config(
    page_title="Groww Feedback Intelligence",
    page_icon="📈",
    layout="wide",
)

EMAIL_SUBJECT = "Weekly Product Pulse + Customer Clarification — Groww"


# --------------------------------------------------------------------------
# Session state helpers
# --------------------------------------------------------------------------


def init_state() -> None:
    defaults = {
        "result": None,
        "source_label": "",
        "pulse_text": "",
        "explanation_text": "",
        "doc_request_id": None,
        "email_request_id": None,
        "doc_receipt": None,
        "email_receipt": None,
        "doc_payload": None,
        "email_body": "",
        "email_recipient": "",
        "run_log": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def store() -> ApprovalStore:
    return ApprovalStore()


def log(message: str) -> None:
    st.session_state["run_log"].append(
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} — {message}"
    )


def find_collected_csv() -> Optional[Path]:
    candidates = sorted(COLLECTED_DIR.glob("*.csv"))
    return candidates[0] if candidates else None


def find_sample_csv() -> Optional[Path]:
    sample = CONFIG_ROOT / "data" / "sample"
    candidates = sorted(sample.glob("*.csv"))
    return candidates[0] if candidates else None


def safe_frame(rows: list[dict]):
    """Build a DataFrame that Arrow can always serialise (mixed types)."""
    import pandas as pd

    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].map(lambda value: "" if value is None else str(value))
    return frame


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------


def show_table(rows):
    """Render a table of dicts (Arrow-safe)."""
    st.dataframe(safe_frame(rows), width="stretch", hide_index=True)

def render_sidebar() -> dict:
    st.sidebar.title("📈 Groww Feedback Intelligence")
    st.sidebar.caption(
        "Real public customer reviews → validated insights → verified fee explanation "
        "→ human-approved documentation entry and email draft."
    )

    settings = get_settings()
    llm = LLMClient()
    st.sidebar.subheader("1 · Data source")
    source_choice = st.sidebar.radio(
        "Choose the review dataset",
        ["Collected App Store reviews", "Upload my own CSV", "Bundled sample"],
        index=0,
    )

    uploaded_path: Optional[Path] = None
    if source_choice == "Upload my own CSV":
        uploader = st.sidebar.file_uploader(
            "Review CSV", type=["csv"], help="review_id, date, review_text, rating, …"
        )
        if uploader is not None:
            target = Path(st.session_state.get("upload_dir", PROJECT_ROOT / "data" / "state"))
            target.mkdir(parents=True, exist_ok=True)
            uploaded_path = target / uploader.name
            uploaded_path.write_bytes(uploader.getvalue())
    elif source_choice == "Bundled sample":
        sample = find_sample_csv()
        if sample is None:
            st.sidebar.error("No bundled sample CSV found.")
        else:
            st.sidebar.caption(f"Using `{sample.name}`")
    else:
        collected = find_collected_csv()
        if collected is None:
            st.sidebar.error(
                "No collected dataset found. Run `python scripts/collect_appstore_reviews.py`."
            )
        else:
            st.sidebar.caption(f"Using `{collected.name}`")

    st.sidebar.subheader("2 · Analysis window")
    weeks = st.sidebar.slider(
        "Recency window (weeks)",
        min_value=settings.weeks_min,
        max_value=settings.weeks_max,
        value=settings.default_weeks,
        help="Reviews older than this are excluded from the analysis and reported separately.",
    )
    as_of = st.sidebar.date_input(
        "As of (last day of the window)",
        value=date.today(),
        help="Reviews dated after this day are excluded.",
    )

    st.sidebar.subheader("3 · Configuration")
    if llm.available:
        st.sidebar.success(f"LLM active: {llm.describe()}")
    else:
        st.sidebar.warning(
            "Offline deterministic mode — no LLM credentials found. Themes and reports are "
            "composed from the validated data with a documented rule-based method."
        )
    st.sidebar.caption("Set GROQ_API_KEY (or OPENAI_API_KEY) to enable LLM analysis.")

    doc_choice = get_documentation_appender()
    mail_choice = get_email_drafter()
    if doc_choice.simulated:
        st.sidebar.warning("Documentation service: SIMULATION (no Notion credentials)")
    else:
        st.sidebar.success(f"Documentation service: {doc_choice.name}")
    if mail_choice.simulated:
        st.sidebar.warning("Email service: SIMULATION (no Gmail credentials)")
    else:
        st.sidebar.success(f"Email service: {mail_choice.name}")

    st.sidebar.divider()
    st.sidebar.caption("Official sources used for verification:")
    for url in OFFICIAL_SOURCE_URLS[:5]:
        st.sidebar.markdown(f"- [{url.replace('https://groww.in', '')}]({url})")

    return {
        "source_choice": source_choice,
        "uploaded_path": uploaded_path,
        "weeks": weeks,
        "as_of": as_of,
    }


# --------------------------------------------------------------------------
# Tabs
# --------------------------------------------------------------------------


def render_validation_tab(result: PipelineResult) -> None:
    report = result.validation
    st.subheader("Data quality")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Rows read", report.rows_read)
    col2.metric("Accepted", report.accepted)
    col3.metric("Excluded", report.excluded)
    col4.metric("Duplicates removed", report.duplicates_removed)

    if report.window_start and report.window_end:
        st.caption(
            f"Window: {report.window_start.isoformat()} → {report.window_end.isoformat()} · "
            f"analysed reviews: {report.date_range_start} → {report.date_range_end}"
        )

    with st.expander("Column mapping (normalised names)"):
        st.json(report.column_map or {})

    if report.warnings:
        with st.expander(f"Validation warnings ({len(report.warnings)})"):
            for warning in report.warnings[:50]:
                st.write(f"• {warning}")

    if report.excluded_records:
        with st.expander(f"Excluded records with reasons ({len(report.excluded_records)})"):
            frame = [
                {
                    "row": item.row_number,
                    "review_id": item.review_id,
                    "reason_code": item.reason_code,
                    "reason": item.reason,
                }
                for item in report.excluded_records[:500]
            ]
            show_table(frame)

    if result.reviews:
        st.markdown("**Sample of the accepted reviews**")
        st.dataframe(
            show_table([
                {
                    "review_id": review.review_id,
                    "date": review.date.isoformat(),
                    "rating": review.rating,
                    "product_area": review.product_area.value,
                    "platform": review.platform.value,
                    "review_text": review.review_text[:220],
                    "source_url": review.source_url,
                }
                for review in result.reviews[:200]
            ]),
            width="stretch",
            hide_index=True,
        )
        csv_bytes = "\n".join(
            [
                "review_id,date,review_text,rating,product_area,platform,source,source_url",
                *[
                    f'"{review.review_id}","{review.date.isoformat()}","{review.review_text}",'
                    f'{review.rating if review.rating is not None else ""},'
                    f'"{review.product_area.value}","{review.platform.value}",'
                    f'"{review.source or ""}","{review.source_url or ""}"'
                    for review in result.reviews
                ],
            ]
        ).encode("utf-8")
        st.download_button(
            "Download the validated review set (CSV)",
            data=csv_bytes,
            file_name="validated_reviews.csv",
            mime="text/csv",
        )


def render_themes_tab(result: PipelineResult) -> None:
    analysis = result.analysis
    mode_label = (
        "LLM-generated themes (names and summaries are interpretation; counts are measured)"
        if analysis.mode.value == "llm"
        else "Rule-based themes from the documented keyword taxonomy (no LLM configured)"
    )
    st.subheader("Themes")
    st.caption(mode_label)

    if not analysis.themes:
        st.warning("No themes could be derived from the accepted reviews.")
        return

    rows = []
    for ranking in analysis.rankings:
        theme = next((t for t in analysis.themes if t.theme_id == ranking.theme_id), None)
        rows.append(
            {
                "rank": ranking.rank,
                "theme": ranking.name,
                "reviews (measured)": ranking.count,
                "frequency": ranking.frequency_score,
                "recency": ranking.recency_score,
                "severity": ranking.severity_score,
                "rating": ranking.rating_score,
                "evidence quality": ranking.evidence_quality_score,
                "score /100": ranking.total_score,
                "interpretation": (theme.interpretation or theme.summary)[:120] if theme else "",
            }
        )
    show_table(rows)

    with st.expander("How themes are ranked (criteria and weights)"):
        st.json(analysis.ranking_criteria.get("weights", {}))
        st.markdown(analysis.ranking_criteria.get("description", ""))

    for theme in analysis.themes:
        with st.expander(f"#{theme.measured_count} reviews — {theme.name}"):
            st.write(f"**Summary:** {theme.summary}")
            if theme.interpretation:
                st.write(f"**Interpretation:** {theme.interpretation}")
            if theme.keywords:
                st.caption("Keywords: " + ", ".join(theme.keywords))
            from groww_intel.analysis import temporal_comparison

            if result.window_start and result.window_end:
                st.caption(
                    "Temporal comparison: "
                    + temporal_comparison(theme, result.window_start, result.window_end)
                )
            evidence = [
                {
                    "review_id": e.review_id,
                    "date": e.date.isoformat(),
                    "rating": e.rating,
                    "product_area": e.product_area.value,
                    "excerpt": e.excerpt,
                    "source_url": e.source_url,
                }
                for e in theme.evidence[:60]
            ]
            show_table(evidence)

    if analysis.model_notes:
        with st.expander("Model / pipeline notes"):
            for note in analysis.model_notes:
                st.write(f"• {note}")


def render_quotes_tab(result: PipelineResult) -> None:
    st.subheader("Verbatim customer quotations")
    st.caption(
        "Every quotation below is an exact substring of the uploaded review text and is "
        "shown with its review id and source link."
    )
    from groww_intel.analysis import verify_quotation

    if not result.analysis.quotations:
        st.warning("No quotations could be extracted from the accepted reviews.")
        return

    for index, quote in enumerate(result.analysis.quotations, start=1):
        verified = verify_quotation(quote.excerpt, result.reviews)
        with st.container(border=True):
            st.markdown(f"**{index}. “{quote.excerpt}”**")
            st.caption(
                f"review_id `{quote.review_id}` · {quote.date.isoformat()} · rating "
                f"{quote.rating if quote.rating is not None else 'n/a'} · "
                f"{quote.product_area.value} · "
                f"{'verbatim match confirmed' if verified else 'NOT FOUND IN DATASET'}"
            )
            if quote.source_url:
                st.markdown(f"[Open the source review page]({quote.source_url})")


def render_fee_tab(result: PipelineResult) -> None:
    fee = result.fee_result
    st.subheader("Fee-issue investigation")
    st.caption(
        "The issue is discovered from the reviews. A candidate is only reported as "
        "recurring when it clears every condition of the evidence threshold."
    )

    if fee.selected is None:
        st.error("No recurring fee issue met the evidence threshold.")
        if fee.limitation_note:
            st.write(fee.limitation_note)
    else:
        issue = fee.selected
        st.success(f"Recurring fee issue identified: {issue.label}")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Supporting reviews", len(issue.supporting_review_ids))
        col2.metric("Explicit fee mentions", issue.explicit_mentions)
        col3.metric("Distinct weeks", issue.distinct_weeks)
        col4.metric("Distinct authors", issue.distinct_authors)
        st.write(f"**Product area:** {issue.product_area.value}")
        st.write(f"**Reading of the reviews:** {issue.interpretation}")
        if issue.mean_rating is not None:
            st.caption(
                f"Mean rating of the supporting reviews: {issue.mean_rating:.1f} · "
                f"share rated 2 or lower: {issue.low_rating_share:.0%}"
            )

        st.markdown("**Evidence threshold**")
        threshold_rows = [
            {
                "condition": name,
                "value": detail.get("value"),
                "required": detail.get("required"),
                "passed": detail.get("passed"),
            }
            for name, detail in issue.threshold_detail.items()
        ]
        show_table(threshold_rows)

        st.markdown("**Representative quotations (verbatim)**")
        for quote in issue.quotes:
            st.markdown(f"> “{quote.excerpt}”")
            st.caption(
                f"review_id `{quote.review_id}` · {quote.date.isoformat()} · rating "
                f"{quote.rating if quote.rating is not None else 'n/a'} · "
                f"{quote.product_area.value}"
            )

        if issue.other_area_review_ids:
            st.caption(
                "Reviews naming the same fee in a different product area were **not** merged "
                f"into this finding ({len(issue.other_area_review_ids)} ids: "
                f"{', '.join(issue.other_area_review_ids[:8])})."
            )
        if issue.related_unclassified_review_ids:
            st.caption(
                "Additional reviews mention this fee without naming a product area, so they "
                "are reported separately and are **not** counted in the headline number "
                f"({len(issue.related_unclassified_review_ids)} ids: "
                f"{', '.join(issue.related_unclassified_review_ids[:8])})."
            )

        with st.expander("Supporting reviews (exact source evidence)"):
            by_id = {r.review_id: r for r in result.reviews}
            st.dataframe(
                show_table([
                    {
                        "review_id": rid,
                        "date": by_id[rid].date.isoformat(),
                        "rating": by_id[rid].rating,
                        "product_area": by_id[rid].product_area.value,
                        "review_text": by_id[rid].review_text[:400],
                        "source_url": by_id[rid].source_url,
                    }
                    for rid in issue.supporting_review_ids
                    if rid in by_id
                ]),
                width="stretch",
                hide_index=True,
            )

    with st.expander("All fee candidates (including those that failed the threshold)"):
        st.dataframe(
            show_table([
                {
                    "candidate": candidate.label,
                    "reviews": len(candidate.supporting_review_ids),
                    "explicit mentions": candidate.explicit_mentions,
                    "weeks": candidate.distinct_weeks,
                    "authors": candidate.distinct_authors,
                    "meets threshold": candidate.meets_threshold,
                }
                for candidate in fee.candidates
            ]),
            width="stretch",
            hide_index=True,
        )

    with st.expander("Evidence threshold definition"):
        st.json(fee.evidence_threshold)


def render_sources_tab(result: PipelineResult) -> None:
    verification = result.verification
    st.subheader("Official source verification")
    if verification.last_checked:
        st.caption(f"Last checked: {verification.last_checked.isoformat()}")
    else:
        st.warning("No source could be checked.")

    st.dataframe(
        show_table([
            {
                "source": source.title,
                "url": source.url,
                "status": source.fetch_status,
                "checked": source.checked_at.isoformat() if source.checked_at else "",
                "facts verified": ", ".join(source.facts_verified) or "-",
                "note": source.note[:120],
            }
            for source in verification.sources
        ]),
        width="stretch",
        hide_index=True,
    )

    st.markdown("**Fee facts and their verification status**")
    st.dataframe(
        show_table([
            {
                "fact": fact.label,
                "value": fact.value,
                "status": fact.confidence,
                "source": fact.source_url,
                "evidence excerpt": fact.quote[:160],
                "note": fact.note[:120],
            }
            for fact in verification.facts
        ]),
        width="stretch",
        hide_index=True,
    )
    if verification.unverified_claims:
        st.warning(
            "Not verified against official sources, therefore excluded from the "
            f"customer-facing explanation: {', '.join(verification.unverified_claims)}"
        )


def render_reports_tab(result: PipelineResult) -> None:
    st.subheader("Reports")
    pulse = result.pulse
    explanation = result.explanation
    if pulse is None or explanation is None:
        st.warning("The reports have not been generated yet.")
        return

    settings = get_settings()

    st.markdown("### 🧭 Internal weekly product pulse")
    st.caption(
        f"Internal only — not for customers. Mode: {pulse.mode.value}. "
        f"Limit: {settings.pulse_max_words} words."
    )
    pulse_text = st.text_area(
        "Weekly pulse (editable)",
        value=st.session_state.get("pulse_text") or pulse.body_markdown,
        height=340,
        key="pulse_editor",
    )
    st.session_state["pulse_text"] = pulse_text
    words = count_words(pulse_text)
    if words > settings.pulse_max_words:
        st.error(f"{words} words — over the {settings.pulse_max_words}-word limit.")
    else:
        st.success(f"{words} / {settings.pulse_max_words} words")
    for warning in pulse.warnings:
        st.warning(warning)

    st.markdown("### 💬 Customer-facing fee explanation")
    st.caption(
        f"Reusable support snippet. Mode: {explanation.mode.value}. "
        f"Limit: {settings.explanation_max_bullets} bullets."
    )
    bullet_lines = st.session_state.get("explanation_text") or "\n".join(explanation.bullets)
    explanation_text = st.text_area(
        "Customer explanation (one bullet per line)",
        value=bullet_lines,
        height=260,
        key="explanation_editor",
    )
    st.session_state["explanation_text"] = explanation_text
    bullets = [line.strip("- ").strip() for line in explanation_text.splitlines() if line.strip()]
    if len(bullets) > settings.explanation_max_bullets:
        st.error(f"{len(bullets)} bullets — over the {settings.explanation_max_bullets}-bullet limit.")
    else:
        st.success(f"{len(bullets)} / {settings.explanation_max_bullets} bullets")
    if explanation.last_checked:
        st.caption(f"Last checked: {explanation.last_checked.isoformat()}")
    for link in explanation.source_links:
        st.markdown(f"- [{link}]({link})")
    if explanation.excluded_claims:
        with st.expander("Claims excluded because they could not be verified"):
            for claim in explanation.excluded_claims:
                st.write(f"• {claim}")

    st.download_button(
        "Download the weekly pulse (Markdown)",
        data=pulse_text.encode("utf-8"),
        file_name="weekly_product_pulse.md",
        mime="text/markdown",
    )
    st.download_button(
        "Download the customer explanation (Markdown)",
        data=("\n".join(f"- {b}" for b in bullets)).encode("utf-8"),
        file_name="customer_fee_explanation.md",
        mime="text/markdown",
    )


# --------------------------------------------------------------------------
# Approval + execution
# --------------------------------------------------------------------------


def current_doc_content(result: PipelineResult) -> tuple[str, object]:
    """Build the documentation record + rendered markdown from the current UI state."""
    pulse_text = st.session_state.get("pulse_text") or result.pulse.body_markdown
    explanation_text = st.session_state.get("explanation_text") or "\n".join(
        result.explanation.bullets
    )
    bullets = [line.strip("- ").strip() for line in explanation_text.splitlines() if line.strip()]
    issue = result.fee_result.selected
    issue_text = (
        f"{issue.label} — {len(issue.supporting_review_ids)} supporting reviews in "
        f"{issue.product_area.value}"
        if issue
        else (result.fee_result.limitation_note or "No recurring fee issue identified")
    )
    supporting = (
        [f"{rid} ({result.validation.file_name})" for rid in issue.supporting_review_ids]
        if issue
        else []
    )
    record = build_documentation_payload(
        pulse_body=pulse_text,
        fee_issue=issue_text,
        explanation_bullets=bullets,
        source_links=result.explanation.source_links,
        top_themes=result.pulse.leading_themes,
        review_date_range=result.review_date_range,
        reviews_analyzed=len(result.reviews),
        supporting_evidence=supporting,
        entry_date=result.window_end,
    )
    return render_documentation_markdown(record), record


def current_email_body(result: PipelineResult) -> str:
    pulse_text = st.session_state.get("pulse_text") or result.pulse.body_markdown
    explanation_text = st.session_state.get("explanation_text") or "\n".join(
        result.explanation.bullets
    )
    bullets = [line.strip("- ").strip() for line in explanation_text.splitlines() if line.strip()]
    links = "\n".join(f"- {link}" for link in result.explanation.source_links)
    checked = (
        result.explanation.last_checked.isoformat()
        if result.explanation.last_checked
        else "not recorded"
    )
    return (
        f"INTERNAL — Weekly Product Pulse\n\n"
        f"{pulse_text}\n\n"
        f"{'-' * 60}\n"
        f"CUSTOMER-FACING FEE EXPLANATION (reusable support snippet)\n\n"
        + "\n".join(f"- {bullet}" for bullet in bullets)
        + f"\n\nVerified official sources (last checked: {checked}):\n{links}\n"
    )


def render_approvals_tab(result: PipelineResult) -> None:
    st.subheader("Approval-gated external actions")
    st.caption(
        "Nothing is written to an external service until a human approves the exact "
        "content below. Editing the content invalidates a previous approval."
    )

    approval_store = store()
    doc_choice = get_documentation_appender()
    mail_choice = get_email_drafter()

    # ---------------- documentation append ----------------
    st.markdown("### 1 · Append to the documentation service")
    doc_content, doc_record = current_doc_content(result)
    st.session_state["doc_payload"] = doc_record.model_dump(mode="json")
    doc_destination = doc_choice.service.destination if hasattr(doc_choice.service, "destination") else (
        "Notion MCP server (https://mcp.notion.com/mcp)" if not doc_choice.simulated else "local simulation file"
    )

    doc_request_id = st.session_state.get("doc_request_id")
    doc_request = approval_store.get(doc_request_id) if doc_request_id else None
    if doc_request is not None and doc_request.content != doc_content:
        approval_store.update_content(doc_request.request_id, doc_content)
        doc_request = approval_store.get(doc_request_id)
        st.warning("The content changed after approval — the previous approval was invalidated.")
    if doc_request is None:
        doc_request = approval_store.create_request(
            action="documentation_append",
            destination=doc_destination,
            content=doc_content,
            summary=f"Weekly product pulse ({result.review_date_range})",
            details={"reviews": len(result.reviews)},
        )
        st.session_state["doc_request_id"] = doc_request.request_id
        log(f"Created approval request {doc_request.request_id} for the documentation append.")

    with st.expander("Review the exact content that will be written", expanded=True):
        st.code(doc_request.content, language="markdown")
        st.caption(f"Destination: {doc_request.destination} · Action: append a new entry (no overwrite)")
        st.caption(f"Content hash: {doc_request.content_hash[:16]}…")

    col1, col2 = st.columns(2)
    approve_doc = col1.button("✅ Approve documentation append", key="approve_doc")
    reject_doc = col2.button("⛔ Reject documentation append", key="reject_doc")
    if approve_doc:
        approval_store.approve(doc_request.request_id, note="Approved from the Streamlit app")
        log(f"Approved {doc_request.request_id}")
        st.success("Approved.")
    if reject_doc:
        approval_store.reject(doc_request.request_id, note="Rejected from the Streamlit app")
        log(f"Rejected {doc_request.request_id}")
        st.error("Rejected — this action will not run.")

    doc_request = approval_store.get(doc_request.request_id)
    if doc_request and doc_request.status == "approved":
        if st.button("📤 Execute documentation append", key="exec_doc"):
            try:
                approval_store.assert_executable(doc_request, doc_content)
                receipt = doc_choice.service.append(doc_record, doc_content)
                approval_store.record_execution(doc_request, receipt)
                st.session_state["doc_receipt"] = receipt.model_dump(mode="json")
                log(f"Executed {doc_request.request_id}: {receipt.status}")
                if receipt.status == "simulated":
                    st.warning(receipt.detail)
                elif receipt.status == "success":
                    st.success(f"Appended successfully. External id: {receipt.external_id}")
                else:
                    st.error(receipt.detail)
            except ApprovalError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"The documentation append failed: {exc}")
    elif doc_request:
        st.info(f"Status: **{doc_request.status}** — execution is blocked until approved.")

    if st.session_state.get("doc_receipt"):
        st.json(st.session_state["doc_receipt"])

    st.divider()

    # ---------------- email draft ----------------
    st.markdown("### 2 · Create an email draft")
    recipient = st.text_input(
        "Recipient (optional — leave blank if unknown; nothing is sent either way)",
        value=st.session_state.get("email_recipient", ""),
        key="email_recipient_input",
    )
    st.session_state["email_recipient"] = recipient.strip()
    email_body = current_email_body(result)
    st.session_state["email_body"] = email_body

    email_request_id = st.session_state.get("email_request_id")
    email_request = approval_store.get(email_request_id) if email_request_id else None
    email_destination = (
        "Gmail (MCP)" if not mail_choice.simulated else "local simulation file"
    )
    if email_request is not None and email_request.content != email_body:
        approval_store.update_content(email_request.request_id, email_body)
        email_request = approval_store.get(email_request_id)
        st.warning("The email draft changed after approval — the previous approval was invalidated.")
    if email_request is None:
        email_request = approval_store.create_request(
            action="email_draft",
            destination=email_destination,
            content=email_body,
            summary=EMAIL_SUBJECT,
            details={"recipient": recipient.strip()},
        )
        st.session_state["email_request_id"] = email_request.request_id
        log(f"Created approval request {email_request.request_id} for the email draft.")

    with st.expander("Review the exact draft that will be created", expanded=True):
        st.text_input("Subject", value=EMAIL_SUBJECT, disabled=True)
        st.code(email_request.content, language="markdown")
        st.caption(
            f"Destination: {email_request.destination} · Action: create a draft (never send) · "
            f"Recipient: {recipient.strip() or '(blank)'}"
        )

    col1, col2 = st.columns(2)
    approve_mail = col1.button("✅ Approve email draft", key="approve_mail")
    reject_mail = col2.button("⛔ Reject email draft", key="reject_mail")
    if approve_mail:
        approval_store.approve(email_request.request_id, note="Approved from the Streamlit app")
        log(f"Approved {email_request.request_id}")
        st.success("Approved.")
    if reject_mail:
        approval_store.reject(email_request.request_id, note="Rejected from the Streamlit app")
        log(f"Rejected {email_request.request_id}")
        st.error("Rejected — this action will not run.")

    email_request = approval_store.get(email_request.request_id)
    if email_request and email_request.status == "approved":
        if st.button("📝 Create the email draft", key="exec_mail"):
            try:
                approval_store.assert_executable(email_request, email_body)
                receipt = mail_choice.service.create_draft(
                    EMAIL_SUBJECT, email_body, recipient.strip() or None
                )
                approval_store.record_execution(email_request, receipt)
                st.session_state["email_receipt"] = receipt.model_dump(mode="json")
                log(f"Executed {email_request.request_id}: {receipt.status}")
                if receipt.status == "simulated":
                    st.warning(receipt.detail)
                elif receipt.status == "success":
                    st.success(f"Draft created. External id: {receipt.external_id}. The email was not sent.")
                else:
                    st.error(receipt.detail)
            except ApprovalError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Creating the draft failed: {exc}")
    elif email_request:
        st.info(f"Status: **{email_request.status}** — execution is blocked until approved.")

    if st.session_state.get("email_receipt"):
        st.json(st.session_state["email_receipt"])

    st.divider()
    with st.expander("Audit log (append-only)"):
        if approval_store.audit_path.exists():
            entries = approval_store.audit_path.read_text(encoding="utf-8").splitlines()[-40:]
            st.code("\n".join(entries), language="json")
        else:
            st.caption("No audit entries yet.")
    with st.expander("All approval requests"):
        requests = approval_store.list_requests()
        st.dataframe(
            show_table([
                {
                    "request_id": r.request_id,
                    "action": r.action,
                    "destination": r.destination,
                    "status": r.status,
                    "decided_by": r.decided_by,
                    "decided_at": r.decided_at,
                    "content_hash": r.content_hash[:12],
                }
                for r in requests
            ]),
            width="stretch",
            hide_index=True,
        )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> None:
    init_state()
    ensure_dirs()
    settings = get_settings()
    options = render_sidebar()

    st.title("Groww — Customer Feedback Intelligence & Support Communication")
    st.caption(
        "Evidence-driven pipeline: real public reviews → validated dataset → themes → "
        "fee-issue verification against official Groww sources → internal pulse + "
        "customer-facing clarification → human-approved documentation entry and email draft."
    )

    source_choice = options["source_choice"]
    csv_path: Optional[Path] = None
    label = ""
    if source_choice == "Upload my own CSV":
        csv_path = options["uploaded_path"]
        label = options["uploaded_path"].name if csv_path else ""
    elif source_choice == "Bundled sample":
        csv_path = find_sample_csv()
        label = csv_path.name if csv_path else ""
    else:
        csv_path = find_collected_csv()
        label = csv_path.name if csv_path else ""

    run_clicked = st.button(
        "▶ Run the analysis",
        type="primary",
        disabled=csv_path is None,
    )

    if run_clicked and csv_path is not None:
        with st.spinner("Validating the CSV and analysing the reviews…"):
            try:
                result = run_pipeline(
                    csv_path,
                    weeks=options["weeks"],
                    as_of=options["as_of"],
                    client=LLMClient(),
                )
            except IngestionError as exc:
                st.error(f"The CSV could not be read: {exc}")
                return
            except Exception as exc:  # pragma: no cover - defensive
                st.error(f"The analysis failed: {exc}")
                return
        st.session_state["result"] = result
        st.session_state["source_label"] = label
        st.session_state["pulse_text"] = result.pulse.body_markdown if result.pulse else ""
        st.session_state["explanation_text"] = (
            "\n".join(result.explanation.bullets) if result.explanation else ""
        )
        st.session_state["doc_request_id"] = None
        st.session_state["email_request_id"] = None
        log(f"Ran the pipeline over {label}")
        st.success(
            f"Analysis complete for {label}: {result.validation.accepted} reviews accepted, "
            f"{result.validation.excluded} excluded, "
            f"{result.validation.duplicates_removed} duplicates removed."
        )
        if result.errors:
            for error in result.errors:
                st.warning(error)

    result: Optional[PipelineResult] = st.session_state.get("result")
    if result is None:
        st.info(
            "Choose a dataset on the left and press **Run the analysis**. "
            "The bundled sample and the collected App Store dataset are real public "
            "customer reviews; nothing on this screen is hard-coded."
        )
        return

    st.caption(
        f"Dataset: **{st.session_state.get('source_label')}** · "
        f"window **{options['weeks']} weeks** ending **{options['as_of']}** · "
        f"analysis mode: **{result.analysis.mode.value}**"
    )

    tabs = st.tabs(
        [
            "1 · Data quality",
            "2 · Themes",
            "3 · Quotations",
            "4 · Fee issue",
            "5 · Sources",
            "6 · Reports",
            "7 · Approvals & actions",
        ]
    )
    with tabs[0]:
        render_validation_tab(result)
    with tabs[1]:
        render_themes_tab(result)
    with tabs[2]:
        render_quotes_tab(result)
    with tabs[3]:
        render_fee_tab(result)
    with tabs[4]:
        render_sources_tab(result)
    with tabs[5]:
        render_reports_tab(result)
    with tabs[6]:
        render_approvals_tab(result)


if __name__ == "__main__":
    main()
