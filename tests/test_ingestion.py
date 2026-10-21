"""CSV ingestion, validation, de-duplication and date filtering."""

from __future__ import annotations

from datetime import date

import pytest

from groww_intel.ingestion import (
    IngestionError,
    build_column_map,
    infer_product_area,
    read_reviews_csv,
)
from groww_intel.schemas import ProductArea


def test_reads_canonical_csv(csv_factory):
    path = csv_factory(
        [
            {
                "review_id": "a1",
                "date": "2026-09-10",
                "review_text": "Brokerage charge is too high for me.",
                "rating": "2",
                "product_area": "Stocks (delivery)",
                "platform": "iOS App Store",
                "source": "App Store",
                "source_url": "https://example.invalid/a1",
            }
        ]
    )
    result = read_reviews_csv(path, as_of=date(2026, 10, 21), weeks=12)
    assert result.report.accepted == 1
    assert result.report.excluded == 0
    review = result.reviews[0]
    assert review.review_id == "a1"
    assert review.rating == 2.0
    assert review.product_area is ProductArea.STOCKS_DELIVERY
    assert review.source_url == "https://example.invalid/a1"


def test_normalises_alternative_column_names(csv_factory):
    path = csv_factory(
        [
            {
                "id": "b1",
                "created_at": "2026-09-11",
                "content": "App crashes when I open the portfolio screen.",
                "stars": "1",
                "link": "https://example.invalid/b1",
            }
        ],
        fieldnames=["id", "created_at", "content", "stars", "link"],
    )
    result = read_reviews_csv(path, as_of=date(2026, 10, 21), weeks=12)
    assert result.report.accepted == 1
    review = result.reviews[0]
    assert review.review_id == "b1"
    assert review.date == date(2026, 9, 11)
    assert review.rating == 1.0


def test_column_map_is_exposed():
    mapping = build_column_map(["Review ID", "Review Date", "Review Text", "Rating"])
    assert mapping["review_id"] == "Review ID"
    assert mapping["date"] == "Review Date"
    assert mapping["review_text"] == "Review Text"
    assert mapping["rating"] == "Rating"


def test_excludes_records_without_text(csv_factory):
    path = csv_factory(
        [
            {"review_id": "c1", "date": "2026-09-01", "review_text": "", "rating": "3"},
            {"review_id": "c2", "date": "2026-09-02", "review_text": "ok text", "rating": "3"},
        ]
    )
    result = read_reviews_csv(path, as_of=date(2026, 10, 21), weeks=12)
    assert result.report.accepted == 1
    assert result.report.excluded == 1
    assert result.report.excluded_records[0].reason_code == "empty_review_text"


def test_excludes_unparseable_dates(csv_factory):
    path = csv_factory(
        [
            {"review_id": "d1", "date": "not-a-date", "review_text": "some text", "rating": "3"},
        ]
    )
    result = read_reviews_csv(path, as_of=date(2026, 10, 21), weeks=12)
    assert result.report.accepted == 0
    assert result.report.excluded_records[0].reason_code == "unparseable_date"


def test_removes_exact_duplicates(csv_factory):
    row = {
        "review_id": "e1",
        "date": "2026-09-01",
        "review_text": "Exact same review text about charges.",
        "rating": "2",
        "author": "sam",
    }
    path = csv_factory([row, dict(row, review_id="e2")], fieldnames=list(row))
    result = read_reviews_csv(path, as_of=date(2026, 10, 21), weeks=12)
    assert result.report.accepted == 1
    assert result.report.duplicates_removed == 1
    assert result.report.excluded_records[0].reason_code == "exact_duplicate"


def test_keeps_similar_but_distinct_reviews(csv_factory):
    path = csv_factory(
        [
            {"review_id": "f1", "date": "2026-09-01", "review_text": "Charge is high.", "rating": "2"},
            {"review_id": "f2", "date": "2026-09-02", "review_text": "Charge is high.", "rating": "2"},
        ]
    )
    result = read_reviews_csv(path, as_of=date(2026, 10, 21), weeks=12)
    # different day => not an exact duplicate
    assert result.report.accepted == 2
    assert result.report.duplicates_removed == 0


def test_filters_reviews_outside_the_recency_window(csv_factory):
    path = csv_factory(
        [
            {"review_id": "g1", "date": "2026-09-01", "review_text": "Recent review text.", "rating": "4"},
            {"review_id": "g2", "date": "2026-01-01", "review_text": "Very old review text.", "rating": "4"},
            {"review_id": "g3", "date": "2027-01-01", "review_text": "Future review text.", "rating": "4"},
        ]
    )
    result = read_reviews_csv(path, as_of=date(2026, 10, 21), weeks=12)
    assert result.report.accepted == 1
    assert result.reviews[0].review_id == "g1"
    reasons = [r.reason_code for r in result.report.excluded_records]
    assert reasons.count("outside_recency_window") == 2


def test_missing_review_text_column_raises(csv_factory):
    path = csv_factory([{"id": "h1", "date": "2026-09-01"}], fieldnames=["id", "date"])
    with pytest.raises(IngestionError):
        read_reviews_csv(path, as_of=date(2026, 10, 21), weeks=12)


def test_out_of_range_rating_is_dropped_not_fatal(csv_factory):
    path = csv_factory(
        [
            {"review_id": "i1", "date": "2026-09-01", "review_text": "Some text here.", "rating": "9"},
        ]
    )
    result = read_reviews_csv(path, as_of=date(2026, 10, 21), weeks=12)
    assert result.report.accepted == 1
    assert result.reviews[0].rating is None
    assert any("out of the 1-5 range" in w for w in result.report.warnings)


def test_product_area_inference():
    assert infer_product_area("my mutual fund SIP failed") is ProductArea.MUTUAL_FUNDS
    assert infer_product_area("F&O option chain is slow") is ProductArea.FUTURES_OPTIONS
    assert infer_product_area("charged brokerage for my stock delivery order") is (
        ProductArea.STOCKS_DELIVERY
    )
    assert infer_product_area("nothing relevant here") is ProductArea.UNKNOWN
