"""Generation of the two reports.

* :func:`generate_weekly_pulse` — the **internal** weekly product pulse
  (max 250 words, three leading themes, verbatim quotations, the key issue
  and exactly three recommendations).
* :func:`generate_customer_explanation` — the **customer-facing** explanation
  of the fee confusion (max six bullets, plain language, only facts that
  were verified against official Groww sources).

Both have an LLM path and a deterministic fallback path.  The fallback is not
a placeholder: it composes the report from the validated dataset facts, so
the output is always evidence-backed.  The UI always states which path was
used.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from .config import get_settings
from .llm import LLMClient, LLMError
from .schemas import (
    AnalysisMode,
    AnalysisResult,
    CustomerExplanation,
    FeeIssueResult,
    Recommendation,
    Review,
    SourceVerificationResult,
    ThemeEvidence,
    WeeklyPulse,
)
from .analysis import verify_quotation
from .sources import grounding_violations, verified_source_text

PULSE_SYSTEM = (
    "You are a senior product manager at Groww, an Indian investing platform. "
    "You propose actionable product recommendations from customer-review evidence. "
    "You never invent reviews, numbers, policies or trends, and you never claim that "
    "a trend increased or decreased. You answer with a single JSON object and nothing else."
)

PULSE_USER = """Analysis window: {start} to {end}. Reviews analysed: {n}.

Leading themes (counts are measured from the dataset):
{themes}

Verbatim customer quotations (these are the only quotations that exist):
{quotes}

Most important observed issue:
{issue}

Propose exactly {recs} recommendations for the product team. For each one:
- title: a short imperative action (max 12 words).
- problem: the customer problem being addressed, grounded in the evidence above.
- intervention: the concrete product change you propose.
- measurable_outcome: one metric the team can track, with a target if you can justify one.

Rules:
- Do not invent quotes. Do not include any quotation in your answer.
- Do not claim that a trend increased or decreased, and do not compare to a previous period.
- Describe each problem factually. Do not assert that one issue caused another, or that a
  fee caused a balance error, unless the evidence explicitly says so.
- Do not invent numbers that are not in the evidence above.
- Keep each field to one short sentence of at most 18 words.
- Do not use bullet lists, markdown headings or long explanations in the fields.

Return JSON:
{{"recommendations": [{{"title": "...", "problem": "...", "intervention": "...", "measurable_outcome": "..."}}]}}"""

#: Words that would assert a trend the dataset cannot support.
TREND_CLAIM_WORDS = (
    "increase", "increased", "increasing", "decrease", "decreased", "decreasing",
    "rising", "rising sharply", "falling", "compared to last", "compared to the previous",
    "year on year", "yoy", "more than last week", "week over week", "since last month",
    "trend is", "trending up", "trending down",
)

#: A model must not turn a co-occurrence in reviews into a causal claim.
CAUSAL_CLAIM_WORDS = (
    "causing", "caused by", "leading to", "resulting in", "due to",
    "because of this", "as a result", "which led to", "consequently",
)

EXPLANATION_SYSTEM = (
    "You write clear, neutral customer-facing explanations for Groww, an Indian "
    "brokerage platform. You only use facts that are supplied to you. You never "
    "invent a fee, a rate or an exception. You answer with a single JSON object "
    "and nothing else."
)

EXPLANATION_USER = """The confusion we need to explain (from customer reviews):
{issue}

Verified facts from official Groww pages (last checked {checked}):
{facts}

Requirements:
- bullets: at most {max_bullets} bullets, each one sentence, plain language, no jargon.
- Cover, where the verified facts allow: what the charge is, why it applies, when it is charged,
  how it is calculated, and any exceptions.
- Only use numbers and rates that appear in the verified facts above. Never invent a number.
- Do not promise refunds, price changes or timelines.

Return JSON: {{"bullets": ["...", "..."]}}"""


def count_words(text: str) -> int:
    """Word count used for the 250-word limit (whitespace separated)."""
    return len([token for token in re.split(r"\s+", text.strip()) if token])


# --------------------------------------------------------------------------
# Weekly product pulse
# --------------------------------------------------------------------------


def _theme_block(analysis: AnalysisResult, limit: int = 3) -> str:
    rankings = {r.theme_id: r for r in analysis.rankings}
    lines: list[str] = []
    for theme in analysis.themes[:limit]:
        ranking = rankings.get(theme.theme_id)
        rated = [e.rating for e in theme.evidence if e.rating is not None]
        rating_text = (
            f", mean rating {sum(rated) / len(rated):.1f}" if rated else ""
        )
        lines.append(
            f"- {theme.name} — {theme.measured_count} reviews{rating_text}. {theme.summary}"
        )
    return "\n".join(lines) or "- (no themes identified)"


def _quote_block(quotations: list[ThemeEvidence]) -> str:
    if not quotations:
        return "(none)"
    lines = []
    for quote in quotations:
        lines.append(
            f'- "{quote.excerpt}" — review {quote.review_id}, {quote.date.isoformat()}, '
            f"rating {quote.rating if quote.rating is not None else 'n/a'}"
        )
    return "\n".join(lines)


def _assemble_pulse(
    *,
    window_start: date,
    window_end: date,
    review_count: int,
    leading: list,
    quotations: list[ThemeEvidence],
    issue_text: str,
    recs: list[Recommendation],
    max_words: int,
) -> str:
    """Build the pulse markdown from measured facts and validated recommendations.

    The theme order, the theme counts, the quotations and their attribution are
    always produced here in code — a model never decides which quote belongs to
    which theme.
    """
    lines: list[str] = []
    lines.append(
        f"**Window** {window_start.isoformat()} → {window_end.isoformat()} · "
        f"{review_count} reviews analysed; theme counts below are measured from the CSV."
    )
    lines.append("")
    lines.append("**Leading themes**")
    for index, theme in enumerate(leading, start=1):
        lines.append(f"{index}. {theme.name} — {theme.measured_count} reviews.")
    lines.append("")
    lines.append("**What customers said (verbatim)**")
    for quote in quotations[:2]:
        excerpt = quote.excerpt
        if len(excerpt) > 105:
            excerpt = excerpt[:105].rsplit(" ", 1)[0] + "…"
        lines.append(f'> "{excerpt}" — review {quote.review_id}, {quote.date.isoformat()}.')
    lines.append("")
    lines.append(f"**Most important observed issue** {issue_text}.")
    lines.append("")
    lines.append("**Recommendations**")
    for index, rec in enumerate(recs, start=1):
        lines.append(
            f"{index}. **{rec.title}** — Problem: {rec.problem} Fix: {rec.intervention} "
            f"Measure: {rec.measurable_outcome}"
        )
    body = "\n".join(lines)
    if count_words(body) > max_words:
        body = _trim_to_word_budget(
            body,
            max_words,
            [t.name for t in leading],
            [t.measured_count for t in leading],
            quotations,
            issue_text,
            recs,
        )
    return body


def _deterministic_pulse(
    reviews: list[Review],
    analysis: AnalysisResult,
    fee_result: FeeIssueResult,
    *,
    window_start: date,
    window_end: date,
    max_words: int = 250,
) -> WeeklyPulse:
    """Compose the pulse from measured facts only (no model involved)."""
    settings = get_settings()
    rankings = {r.theme_id: r for r in analysis.rankings}
    ordered = sorted(
        analysis.themes,
        key=lambda t: rankings[t.theme_id].rank if t.theme_id in rankings else 99,
    )
    leading = ordered[:3]
    quotations = analysis.quotations[: settings.max_quotations]

    issue = fee_result.selected
    if issue:
        issue_text = (
            f"{issue.fee_category.value} in {issue.product_area.value} — "
            f"{len(issue.supporting_review_ids)} reviews, {issue.distinct_weeks} weeks, "
            f"{issue.explicit_mentions} naming the charge explicitly"
        )
    elif fee_result.limitation_note:
        issue_text = "no recurring fee confusion could be evidenced in this window"
    else:
        issue_text = "no fee issue could be isolated from the review set"

    recs = _default_recommendations(reviews, leading, issue)
    body = _assemble_pulse(
        window_start=window_start,
        window_end=window_end,
        review_count=len(reviews),
        leading=leading,
        quotations=quotations,
        issue_text=issue_text,
        recs=recs,
        max_words=max_words,
    )

    warnings: list[str] = []
    if count_words(body) > max_words:  # pragma: no cover - defensive
        warnings.append(f"The pulse is {count_words(body)} words after trimming.")

    return WeeklyPulse(
        title=f"Weekly Product Pulse — Groww ({window_start.isoformat()} → {window_end.isoformat()})",
        body_markdown=body,
        word_count=count_words(body),
        leading_themes=[t.name for t in leading],
        quotations=quotations,
        key_issue=issue_text,
        recommendations=recs,
        mode=AnalysisMode.HEURISTIC,
        warnings=warnings,
    )


def _default_recommendations(
    reviews: list[Review],
    leading: list,
    issue,
) -> list[Recommendation]:
    """Evidence-based default recommendations (used when the LLM is unusable)."""
    settings = get_settings()
    recs: list[Recommendation] = []
    if issue is not None:
        recs.append(
            Recommendation(
                title="Show a per-order charge breakdown",
                problem=(
                    f"{len(issue.supporting_review_ids)} {issue.product_area.value} reviews say "
                    "per-order charges were higher than expected."
                ),
                intervention=(
                    "List brokerage, DP, statutory charges and GST on the order confirmation."
                ),
                measurable_outcome="Charge-related tickets per 1,000 orders (target -50% in 8 weeks).",
            )
        )
        recs.append(
            Recommendation(
                title="State the ₹5 minimum brokerage at order time",
                problem=(
                    "Reviews call brokerage an unexpected flat charge; the documented rule is a "
                    "₹5 minimum."
                ),
                intervention="Show the 'whichever is lower, minimum ₹5' rule on the order ticket.",
                measurable_outcome="Tickets tagged 'brokerage' per 1,000 orders.",
            )
        )
    else:
        recs.append(
            Recommendation(
                title="Keep the charge-confusion baseline running",
                problem="No recurring fee confusion could be evidenced in this window.",
                intervention="Keep tracking charge tickets with the same evidence threshold.",
                measurable_outcome="Charge-related tickets per 1,000 orders, tracked weekly.",
            )
        )
    if leading:
        recs.append(
            Recommendation(
                title=f"Fix the leading theme: {leading[0].name.lower()}",
                problem=(
                    f"{leading[0].measured_count} of {len(reviews)} reviews in the window fall "
                    "into this theme."
                ),
                intervention="Give the owning squad the linked review ids.",
                measurable_outcome="Repeat rate of this theme in the next four weekly reviews.",
            )
        )
    return recs[: settings.recommendations_count]


def _trim_to_word_budget(
    body: str,
    max_words: int,
    theme_names: list[str],
    theme_counts: list[int],
    quotations: list[ThemeEvidence],
    issue_text: str,
    recs: list[Recommendation],
) -> str:
    """Rebuild the pulse with a tighter budget (quotations shorten first)."""

    def render() -> str:
        lines = ["**Leading themes (measured counts)**"]
        for index, (name, count) in enumerate(zip(theme_names[:3], theme_counts[:3]), start=1):
            lines.append(f"{index}. {name} — {count} reviews.")
        lines.append("")
        lines.append("**What customers said (verbatim)**")
        for quote in quotations[:2]:
            excerpt = quote.excerpt
            if len(excerpt) > 100:
                excerpt = excerpt[:100].rsplit(" ", 1)[0] + "…"
            lines.append(f'> "{excerpt}" — review {quote.review_id}.')
        lines.append("")
        lines.append(f"**Most important observed issue** {issue_text}.")
        lines.append("")
        lines.append("**Recommendations**")
        for index, rec in enumerate(recs, start=1):
            lines.append(
                f"{index}. **{rec.title}** — Problem: {rec.problem} Fix: {rec.intervention} "
                f"Measure: {rec.measurable_outcome}"
            )
        return "\n".join(lines)

    body = render()
    while count_words(body) > max_words:
        # Shorten the longest recommendation until the pulse fits the limit.
        longest = max(
            recs,
            key=lambda r: len(r.problem) + len(r.intervention) + len(r.measurable_outcome),
        )
        shortened = False
        for field_name in ("measurable_outcome", "intervention", "problem"):
            value = getattr(longest, field_name)
            words = value.split()
            if len(words) > 6:
                setattr(longest, field_name, _truncate_words(words, len(words) - 4))
                shortened = True
                break
        if not shortened:
            break
        body = render()
    return body


def _truncate_words(words: list[str], keep: int) -> str:
    """Keep the first ``keep`` words and end with an ellipsis when cut."""
    kept = words[:keep]
    text = " ".join(kept).rstrip(",;:(")
    if len(kept) < len(words):
        text = text.rstrip(" ,;:(") + "…"
    return text


def generate_weekly_pulse(
    reviews: list[Review],
    analysis: AnalysisResult,
    fee_result: FeeIssueResult,
    *,
    window_start: date,
    window_end: date,
    client: Optional[LLMClient] = None,
) -> WeeklyPulse:
    """Generate the internal weekly product pulse.

    The *facts* (theme order, theme counts, the quotations and their
    attribution, the observed issue) are always assembled in code.  The model
    is used for the judgement part — the three recommendations — and its
    output is validated before it is used.
    """
    settings = get_settings()
    client = client or LLMClient()

    fallback = _deterministic_pulse(
        reviews,
        analysis,
        fee_result,
        window_start=window_start,
        window_end=window_end,
        max_words=settings.pulse_max_words,
    )
    if not client.available:
        fallback.warnings.append(
            "Generated by the deterministic composer (no LLM credentials configured)."
        )
        return fallback

    rankings = {r.theme_id: r for r in analysis.rankings}
    ordered = sorted(
        analysis.themes,
        key=lambda t: rankings[t.theme_id].rank if t.theme_id in rankings else 99,
    )
    leading = ordered[:3]
    issue = fee_result.selected

    try:
        payload, _ = client.complete_json(
            PULSE_SYSTEM,
            PULSE_USER.format(
                start=window_start.isoformat(),
                end=window_end.isoformat(),
                n=len(reviews),
                themes=_theme_block(analysis),
                quotes=_quote_block(analysis.quotations[: settings.max_quotations]),
                issue=(
                    f"{issue.label} — {len(issue.supporting_review_ids)} reviews"
                    if issue
                    else fee_result.limitation_note or "none identified"
                ),
                recs=settings.recommendations_count,
            ),
        )
    except LLMError as exc:
        fallback.warnings.append(
            f"LLM recommendation generation failed ({exc}); used the deterministic recommendations."
        )
        return fallback

    recs: list[Recommendation] = []
    rejected: list[str] = []
    for item in (payload.get("recommendations") or []):
        if not isinstance(item, dict):
            continue
        try:
            candidate = Recommendation(
                title=str(item.get("title", "")).strip()[:120],
                problem=str(item.get("problem", "")).strip(),
                intervention=str(item.get("intervention", "")).strip(),
                measurable_outcome=str(item.get("measurable_outcome", "")).strip(),
            )
        except Exception:
            continue
        if not (candidate.title and candidate.problem and candidate.intervention and candidate.measurable_outcome):
            continue
        text = " ".join(
            [candidate.title, candidate.problem, candidate.intervention, candidate.measurable_outcome]
        ).lower()
        claims = [word for word in TREND_CLAIM_WORDS if word in text]
        causal = [word for word in CAUSAL_CLAIM_WORDS if word in text]
        if claims:
            rejected.append(f"{candidate.title[:50]} (unsupported trend claim: {claims[0]})")
            continue
        if causal:
            rejected.append(f"{candidate.title[:50]} (unsupported causal wording: {causal[0]})")
            continue
        recs.append(candidate)

    warnings: list[str] = list(fallback.warnings)
    if len(recs) != settings.recommendations_count:
        fallback.warnings.append(
            f"The model returned {len(recs)} usable recommendations; the deterministic ones "
            "were used for the rest."
        )
        defaults = _default_recommendations(reviews, leading, issue)
        for extra in defaults:
            if len(recs) >= settings.recommendations_count:
                break
            recs.append(extra)
        recs = recs[: settings.recommendations_count]
    if rejected:
        warnings.append(
            "Rejected recommendation(s) that asserted an unsupported trend: "
            + "; ".join(rejected[:3])
        )

    body = _assemble_pulse(
        window_start=window_start,
        window_end=window_end,
        review_count=len(reviews),
        leading=leading,
        quotations=analysis.quotations[: settings.max_quotations],
        issue_text=fallback.key_issue,
        recs=recs,
        max_words=settings.pulse_max_words,
    )

    return WeeklyPulse(
        title=fallback.title,
        body_markdown=body,
        word_count=count_words(body),
        leading_themes=fallback.leading_themes,
        quotations=analysis.quotations[: settings.max_quotations],
        key_issue=fallback.key_issue,
        recommendations=recs,
        mode=AnalysisMode.LLM,
        warnings=warnings,
    )


def _find_non_verbatim_quotes(body: str, reviews: list[Review]) -> list[str]:
    """Find quoted strings in the body that do not appear in the review set."""
    candidates = re.findall(r'"([^"]{30,400})"', body)
    non_verbatim = []
    for candidate in candidates:
        if verify_quotation(candidate, reviews) is None:
            non_verbatim.append(candidate)
    return non_verbatim


# --------------------------------------------------------------------------
# Customer-facing explanation
# --------------------------------------------------------------------------


def _deterministic_explanation(
    fee_result: FeeIssueResult,
    verification: SourceVerificationResult,
    *,
    max_bullets: int = 6,
) -> CustomerExplanation:
    """Compose the customer explanation from verified facts only."""
    facts = {f.fact_id: f for f in verification.facts if f.verified}
    issue = fee_result.selected
    area = issue.product_area.value if issue else "equity trading"

    bullets: list[str] = []

    def add(fact_id: str, text: str) -> None:
        if fact_id in facts and len(bullets) < max_bullets:
            bullets.append(text)

    add(
        "equity_brokerage",
        "Brokerage is Groww's fee for processing your order: ₹20 or 0.1% of the order value, "
        "whichever is lower, with a minimum of ₹5 per executed order.",
    )
    add(
        "intraday_minimum",
        "On intraday trades, if the brokerage works out below ₹5, the minimum charged is the "
        "lower of ₹5 or 2.5% of the trade value.",
    )
    add(
        "dp_charges",
        "Every sale from your demat account also carries a DP charge — ₹3.50 (depository, male) "
        "or ₹3.25 (female) plus Groww's ₹16.50 — and Groww waives its ₹16.50 when the sale "
        "value is below ₹100.",
    )
    add(
        "gst",
        "18% GST is added on top of brokerage, DP charges and the other charges Groww lists.",
    )
    add(
        "stamp_duty",
        "Stamp duty is 0.015% of the buying value when you buy for delivery, and ₹0 when you sell.",
    )
    add(
        "stt",
        "STT is a government tax of 0.1% of the transaction value on delivery buys and sells "
        "(0.025% on intraday sells).",
    )
    add(
        "account_maintenance",
        "There is no account opening charge and no account maintenance (AMC) charge on Groww.",
    )
    add(
        "brokerage_calculator",
        "You can see the charges on a trade before placing it with Groww's official brokerage "
        "calculator, and the itemised charges are on your contract note.",
    )
    add(
        "exchange_sebi",
        "Small levies also apply: 0.00297% (NSE) or 0.00375% (BSE) exchange transaction charge, "
        "0.0001% SEBI turnover charge and 0.0001% investor protection fund charge.",
    )
    add(
        "auto_square_off",
        "If an intraday position is not closed by 3:20 PM, Groww squares it off and charges "
        "₹50 per position plus GST.",
    )

    # Keep only bullets that are grounded in the verified source text.
    allowed = verified_source_text(verification)
    kept: list[str] = []
    excluded: list[str] = []
    for bullet in bullets:
        violations = grounding_violations(bullet, allowed)
        if violations:
            excluded.append(f"{bullet} (unverified numbers: {', '.join(violations)})")
            continue
        kept.append(bullet)
    kept = kept[:max_bullets]

    links = []
    for fact_id in ("equity_brokerage", "dp_charges", "gst", "delivery_sell_charges"):
        fact = facts.get(fact_id)
        if fact and fact.source_url not in links:
            links.append(fact.source_url)
    for source in verification.sources:
        if source.fetch_status in {"live", "snapshot"} and source.url not in links:
            links.append(source.url)

    return CustomerExplanation(
        title=f"Understanding the charges on your {area.lower()} trades",
        bullets=kept,
        source_links=links,
        last_checked=verification.last_checked,
        mode=AnalysisMode.HEURISTIC,
        excluded_claims=excluded,
        warnings=["Generated by the deterministic composer (no LLM credentials configured)."],
    )


def generate_customer_explanation(
    fee_result: FeeIssueResult,
    verification: SourceVerificationResult,
    *,
    client: Optional[LLMClient] = None,
) -> CustomerExplanation:
    """Generate the customer-facing explanation of the fee confusion."""
    settings = get_settings()
    client = client or LLMClient()

    fallback = _deterministic_explanation(
        fee_result, verification, max_bullets=settings.explanation_max_bullets
    )
    if not fallback.bullets:
        fallback.warnings.append(
            "No fee fact could be verified against the official sources, so no customer "
            "explanation was produced. This is reported rather than filled with unverified text."
        )
        return fallback

    if not client.available:
        return fallback

    verified_facts = [f for f in verification.facts if f.verified]
    if not verified_facts:
        return fallback

    facts_block = "\n".join(f"- {f.value} (source: {f.source_url})" for f in verified_facts)
    issue = fee_result.selected
    issue_text = (
        f"{issue.label}. {issue.interpretation}"
        if issue
        else fee_result.limitation_note or "no recurring fee issue identified"
    )

    try:
        payload, _ = client.complete_json(
            EXPLANATION_SYSTEM,
            EXPLANATION_USER.format(
                issue=issue_text,
                checked=verification.last_checked.isoformat() if verification.last_checked else "n/a",
                facts=facts_block,
                max_bullets=settings.explanation_max_bullets,
            ),
        )
    except LLMError as exc:
        fallback.warnings.append(
            f"LLM explanation generation failed ({exc}); used the deterministic composer."
        )
        return fallback

    bullets = [str(b).strip() for b in (payload.get("bullets") or []) if str(b).strip()]
    warnings: list[str] = []
    if not bullets:
        fallback.warnings.append("LLM returned no bullets; used the deterministic composer.")
        return fallback
    if len(bullets) > settings.explanation_max_bullets:
        warnings.append(
            f"LLM returned {len(bullets)} bullets; trimmed to the {settings.explanation_max_bullets}-bullet limit."
        )
        bullets = bullets[: settings.explanation_max_bullets]

    allowed = verified_source_text(verification)
    grounded: list[str] = []
    for bullet in bullets:
        violations = grounding_violations(bullet, allowed)
        if violations:
            warnings.append(
                f"Removed a bullet containing a number that is not in the verified sources "
                f"({', '.join(violations)}): {bullet[:80]}"
            )
            continue
        grounded.append(bullet)

    if not grounded:
        fallback.warnings.append(
            "Every LLM bullet failed the number-grounding check; used the deterministic composer."
        )
        return fallback

    return CustomerExplanation(
        title=fallback.title,
        bullets=grounded,
        source_links=fallback.source_links,
        last_checked=verification.last_checked,
        mode=AnalysisMode.LLM,
        excluded_claims=fallback.excluded_claims,
        warnings=warnings,
    )
