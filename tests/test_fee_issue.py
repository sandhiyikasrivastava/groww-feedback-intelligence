"""Fee-issue detection and the evidence threshold."""

from __future__ import annotations

from datetime import date

from groww_intel.fee_issue import (
    charge_signatures,
    detect_fee_signals,
    fee_context_area,
    investigate_fee_issue,
    mentions_generic_charge,
)
from groww_intel.schemas import FeeCategory, ProductArea, Review


def _review(index: int, text: str, day: int, area=ProductArea.STOCKS_DELIVERY, rating=2.0, author=None):
    return Review(
        review_id=f"r{index}",
        date=date(2026, 9, day),
        review_text=text,
        rating=rating,
        product_area=area,
        author=author or f"user{index}",
    )


def test_detects_fee_categories():
    review = _review(1, "Brokerage charge of 20 per order is too much.", 10)
    assert FeeCategory.BROKERAGE in detect_fee_signals(review)
    assert mentions_generic_charge(review)


def test_generic_charge_words_are_detected_but_not_specific():
    review = _review(2, "The charges are a problem for me.", 11)
    assert mentions_generic_charge(review)
    assert detect_fee_signals(review) == set()


def test_recurring_issue_is_detected(fee_reviews):
    result = investigate_fee_issue(fee_reviews, window_end=date(2026, 10, 21))
    assert result.selected is not None
    issue = result.selected
    assert issue.product_area is ProductArea.STOCKS_DELIVERY
    assert len(issue.supporting_review_ids) == 3
    assert issue.explicit_mentions >= 2
    assert issue.distinct_weeks >= 2
    assert issue.distinct_authors >= 2
    assert issue.meets_threshold
    assert issue.quotes


def test_issue_below_threshold_is_not_reported():
    reviews = [_review(1, "Brokerage charge is high.", 10)]
    result = investigate_fee_issue(reviews, window_end=date(2026, 10, 21))
    assert result.selected is None
    assert result.insufficient_evidence
    assert result.limitation_note
    assert result.candidates  # candidates are still shown


def test_same_review_repeated_by_one_author_does_not_reach_the_threshold():
    reviews = [
        _review(1, "Brokerage charge is too high for delivery.", 10, author="same"),
        _review(2, "Brokerage charge is too high for delivery.", 17, author="same"),
        _review(3, "Brokerage charge is too high for delivery.", 24, author="same"),
    ]
    result = investigate_fee_issue(reviews, window_end=date(2026, 10, 21))
    assert result.selected is None
    assert result.insufficient_evidence


def test_product_areas_are_not_mixed():
    reviews = [
        _review(1, "Brokerage charge on my equity delivery order is high.", 10),
        _review(2, "Brokerage charge on my equity delivery order is high.", 17),
        _review(3, "Brokerage charge on my mutual fund SIP is high.", 24, area=ProductArea.MUTUAL_FUNDS),
    ]
    result = investigate_fee_issue(reviews, window_end=date(2026, 10, 21))
    if result.selected is not None:
        assert result.selected.product_area is not None
        areas = {
            fee_context_area(r)
            for r in reviews
            if r.review_id in result.selected.supporting_review_ids
        }
        assert len(areas) == 1
    for candidate in result.candidates:
        assert candidate.product_area in set(ProductArea)


def test_no_fee_language_reports_a_limitation():
    reviews = [_review(1, "The app is slow and support never replies.", 10, area=ProductArea.PLATFORM)]
    result = investigate_fee_issue(reviews, window_end=date(2026, 10, 21))
    assert result.selected is None
    assert result.insufficient_evidence
    assert "no review" in result.limitation_note.lower()


def test_charge_signatures_describe_the_confusion():
    review = _review(1, "Flat 5 rs new Extra Charge for every stock purchase.", 10)
    signatures = charge_signatures(review)
    assert "a flat ₹5 charge" in signatures
    assert "an unexpected extra debit" in signatures


def test_fee_context_area_prefers_the_product_context():
    review = _review(
        1, "Charges applied on my crude oil futures contract were high.", 10, area=ProductArea.UNKNOWN
    )
    assert fee_context_area(review) is ProductArea.COMMODITIES
