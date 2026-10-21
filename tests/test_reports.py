"""Report generation: word limits, bullet limits, grounding and provenance."""

from __future__ import annotations

from datetime import date

from groww_intel.config import get_settings
from groww_intel.reports import (
    count_words,
    generate_customer_explanation,
    generate_weekly_pulse,
)
from groww_intel.schemas import (
    AnalysisMode,
    OfficialSource,
    SourceFact,
    SourceVerificationResult,
)

WINDOW_START = date(2026, 7, 29)
WINDOW_END = date(2026, 10, 21)


def _verification() -> SourceVerificationResult:
    facts = [
        SourceFact(
            fact_id="equity_brokerage",
            label="Equity brokerage",
            value="₹20 or 0.1% of the order value per executed order, whichever is lower, minimum ₹5.",
            source_url="https://groww.in/pricing/",
            quote="Equity Brokerage ₹20 0.1% per executed order whichever is lower minimum ₹5",
            verified=True,
            confidence="verified",
        ),
        SourceFact(
            fact_id="gst",
            label="GST",
            value="18% GST on brokerage, DP charges and other charges.",
            source_url="https://groww.in/help/stocks/sx-pricing/how-much-gst-is-charged",
            quote="The applicable GST rate is 18% on various charges",
            verified=True,
            confidence="verified",
        ),
        SourceFact(
            fact_id="unverified_thing",
            label="Something not confirmed",
            value="42% mystery levy",
            source_url="https://groww.in/pricing/",
            verified=False,
            confidence="not_found",
            note="not present on the page",
        ),
    ]
    sources = [
        OfficialSource(
            url="https://groww.in/pricing/",
            title="Groww pricing",
            purpose="master fee table",
            checked_at=WINDOW_END,
            fetch_status="live",
        ),
        OfficialSource(
            url="https://groww.in/help/stocks/sx-pricing/how-much-gst-is-charged",
            title="Groww help — GST",
            purpose="GST rate",
            checked_at=WINDOW_END,
            fetch_status="live",
        ),
    ]
    return SourceVerificationResult(
        sources=sources, facts=facts, last_checked=WINDOW_END, unverified_claims=["Something not confirmed"]
    )


def test_weekly_pulse_respects_the_word_limit(fee_reviews, unrelated_reviews, offline_llm):
    from groww_intel.analysis import analyze_themes
    from groww_intel.fee_issue import investigate_fee_issue

    analysis = analyze_themes(
        fee_reviews + unrelated_reviews, offline_llm, window_start=WINDOW_START, window_end=WINDOW_END
    )
    fee = investigate_fee_issue(fee_reviews + unrelated_reviews, window_end=WINDOW_END)
    pulse = generate_weekly_pulse(
        fee_reviews + unrelated_reviews,
        analysis,
        fee,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        client=offline_llm,
    )
    assert pulse.word_count <= get_settings().pulse_max_words == 250
    assert count_words(pulse.body_markdown) == pulse.word_count
    assert len(pulse.recommendations) == get_settings().recommendations_count == 3
    for recommendation in pulse.recommendations:
        assert recommendation.problem and recommendation.intervention and recommendation.measurable_outcome
    assert pulse.leading_themes


def test_weekly_pulse_quotes_are_verbatim(fee_reviews, unrelated_reviews, offline_llm):
    from groww_intel.analysis import analyze_themes, verify_quotation
    from groww_intel.fee_issue import investigate_fee_issue

    reviews = fee_reviews + unrelated_reviews
    analysis = analyze_themes(reviews, offline_llm, window_start=WINDOW_START, window_end=WINDOW_END)
    fee = investigate_fee_issue(reviews, window_end=WINDOW_END)
    pulse = generate_weekly_pulse(
        reviews, analysis, fee, window_start=WINDOW_START, window_end=WINDOW_END, client=offline_llm
    )
    for quote in pulse.quotations:
        assert verify_quotation(quote.excerpt, reviews) is not None


def test_customer_explanation_respects_the_bullet_limit(fee_reviews, offline_llm):
    from groww_intel.fee_issue import investigate_fee_issue

    fee = investigate_fee_issue(fee_reviews, window_end=WINDOW_END)
    explanation = generate_customer_explanation(fee, _verification(), client=offline_llm)
    assert 1 <= len(explanation.bullets) <= get_settings().explanation_max_bullets == 6
    assert explanation.last_checked == WINDOW_END
    assert explanation.source_links


def test_customer_explanation_excludes_unverified_numbers(fee_reviews, offline_llm):
    from groww_intel.fee_issue import investigate_fee_issue

    fee = investigate_fee_issue(fee_reviews, window_end=WINDOW_END)
    explanation = generate_customer_explanation(fee, _verification(), client=offline_llm)
    for bullet in explanation.bullets:
        assert "42%" not in bullet
        assert "mystery" not in bullet


def test_customer_explanation_is_empty_when_nothing_is_verified(fee_reviews, offline_llm):
    from groww_intel.fee_issue import investigate_fee_issue

    fee = investigate_fee_issue(fee_reviews, window_end=WINDOW_END)
    empty = SourceVerificationResult(last_checked=WINDOW_END)
    explanation = generate_customer_explanation(fee, empty, client=offline_llm)
    assert explanation.bullets == []
    assert explanation.warnings


def test_word_counter():
    assert count_words("one two three") == 3
    assert count_words("  spaced \n words ") == 2
    assert count_words("") == 0
