"""Theme analysis: limits, ranking, and quotation provenance."""

from __future__ import annotations

from datetime import date

from groww_intel.analysis import (
    analyze_themes,
    extract_quote,
    rank_themes,
    select_quotations,
    temporal_comparison,
    verify_quotation,
)
from groww_intel.config import get_settings
from groww_intel.schemas import ProductArea, Review, Theme, ThemeEvidence

WINDOW_START = date(2026, 7, 29)
WINDOW_END = date(2026, 10, 21)


def _review(index: int, text: str, day: int = 10, rating: float | None = 2.0) -> Review:
    return Review(
        review_id=f"r{index}",
        date=date(2026, 9, min(day, 28)),
        review_text=text,
        rating=rating,
        product_area=ProductArea.STOCKS_DELIVERY,
        source_url=f"https://example.invalid/r{index}",
    )


def test_theme_count_never_exceeds_five(fee_reviews, unrelated_reviews, offline_llm):
    reviews = fee_reviews + unrelated_reviews + [
        _review(i, f"support did not respond to my complaint number {i}")
        for i in range(20)
    ]
    result = analyze_themes(reviews, offline_llm, window_start=WINDOW_START, window_end=WINDOW_END)
    assert len(result.themes) <= get_settings().max_themes == 5
    assert len(result.rankings) <= 5
    assert [r.rank for r in result.rankings] == list(range(1, len(result.rankings) + 1))


def test_theme_counts_are_measured_not_model_claimed(fee_reviews, unrelated_reviews, offline_llm):
    reviews = fee_reviews + unrelated_reviews
    result = analyze_themes(reviews, offline_llm, window_start=WINDOW_START, window_end=WINDOW_END)
    for theme in result.themes:
        assert theme.measured_count == len(theme.evidence)
        assert theme.measured_count == len(set(theme.review_ids))
        for evidence in theme.evidence:
            assert evidence.review_id in {r.review_id for r in reviews}


def test_quotations_are_verbatim(fee_reviews, unrelated_reviews, offline_llm):
    reviews = fee_reviews + unrelated_reviews
    result = analyze_themes(reviews, offline_llm, window_start=WINDOW_START, window_end=WINDOW_END)
    assert result.quotations
    assert len(result.quotations) <= get_settings().max_quotations == 3
    for quote in result.quotations:
        assert verify_quotation(quote.excerpt, reviews) is not None


def test_verify_quotation_rejects_text_that_is_not_in_the_dataset(fee_reviews):
    assert verify_quotation("this sentence is nowhere in the dataset", fee_reviews) is None
    original = fee_reviews[0].review_text
    assert verify_quotation(original, fee_reviews) is not None


def test_extract_quote_returns_a_substring():
    text = "First sentence here. Second sentence with the brokerage detail. Third one."
    quote = extract_quote(text)
    assert quote is not None
    assert quote in " ".join(text.split())


def test_rankings_are_ordered_and_scored():
    theme_a = Theme(
        theme_id="t1",
        name="Charges and fees",
        summary="summary text",
        keywords=["charge"],
        review_ids=["r1", "r2"],
        evidence=[
            ThemeEvidence(review_id="r1", date=date(2026, 9, 20), rating=1, excerpt="a"),
            ThemeEvidence(review_id="r2", date=date(2026, 10, 1), rating=2, excerpt="b"),
        ],
    )
    theme_b = Theme(
        theme_id="t2",
        name="App speed",
        summary="summary text",
        keywords=["slow"],
        review_ids=["r3"],
        evidence=[ThemeEvidence(review_id="r3", date=date(2026, 10, 2), rating=5, excerpt="c")],
    )
    reviews = {
        "r1": _review(1, "charges are high"),
        "r2": _review(2, "charges again"),
        "r3": _review(3, "app is slow"),
    }
    rankings = rank_themes([theme_a, theme_b], reviews, WINDOW_END)
    assert rankings[0].theme_id == "t1"
    assert rankings[0].total_score > rankings[1].total_score
    assert rankings[0].count == 2


def test_temporal_comparison_refuses_to_claim_a_direction_without_data():
    theme = Theme(
        theme_id="t",
        name="Theme name",
        summary="summary text",
        review_ids=["r1"],
        evidence=[ThemeEvidence(review_id="r1", date=date(2026, 9, 1), excerpt="a")],
    )
    message = temporal_comparison(theme, WINDOW_START, WINDOW_END)
    assert "Not enough reviews" in message
    assert "increase" not in message.lower()
    assert "decrease" not in message.lower()


def test_temporal_comparison_reports_a_direction_when_supported():
    evidence = [
        ThemeEvidence(review_id=f"r{i}", date=date(2026, 8, 5 + i), excerpt="x")
        for i in range(6)
    ] + [
        ThemeEvidence(review_id=f"s{i}", date=date(2026, 10, 1 + i), excerpt="y")
        for i in range(9)
    ]
    theme = Theme(
        theme_id="t",
        name="Theme name",
        summary="summary text",
        review_ids=[e.review_id for e in evidence],
        evidence=evidence,
    )
    message = temporal_comparison(theme, WINDOW_START, WINDOW_END)
    assert "More reviews in the second half" in message


def test_select_quotations_prefers_concrete_reviews():
    reviews = [
        _review(1, "Great"),
        _review(2, "Brokerage of 20 per order was charged twice on my delivery order."),
        _review(3, "Nice app"),
    ]
    theme = Theme(
        theme_id="t",
        name="Charges",
        summary="summary text",
        keywords=["brokerage"],
        review_ids=[r.review_id for r in reviews],
        evidence=[
            ThemeEvidence(review_id=r.review_id, date=r.date, rating=r.rating, excerpt=r.review_text)
            for r in reviews
        ],
    )
    chosen = select_quotations(reviews, [theme], limit=1)
    assert chosen[0].review_id == "r2"
