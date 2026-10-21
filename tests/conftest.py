"""Shared fixtures for the test suite."""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from groww_intel.llm import LLMClient  # noqa: E402
from groww_intel.schemas import Review  # noqa: E402

AS_OF = date(2026, 10, 21)
WINDOW_START = date(2026, 7, 29)


def write_csv(path: Path, rows: Iterable[dict], fieldnames: Optional[list[str]] = None) -> Path:
    rows = list(rows)
    if fieldnames is None:
        fieldnames = [
            "review_id", "date", "review_text", "rating", "product_area",
            "platform", "source", "source_url",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


@pytest.fixture()
def csv_factory(tmp_path):
    def _factory(rows: Iterable[dict], name: str = "reviews.csv", fieldnames=None) -> Path:
        return write_csv(tmp_path / name, rows, fieldnames)

    return _factory


@pytest.fixture()
def fee_reviews() -> list[Review]:
    """Reviews that contain a recurring, equity-scoped brokerage complaint."""
    return [
        Review(
            review_id="r1",
            date=date(2026, 9, 10),
            review_text="Flat 5 rs new Extra Charge for every stock purchase. Very annoying.",
            rating=2,
            product_area=__import__(
                "groww_intel.schemas", fromlist=["ProductArea"]
            ).ProductArea.STOCKS_DELIVERY,
            source_url="https://example.invalid/r1",
        ),
        Review(
            review_id="r2",
            date=date(2026, 9, 24),
            review_text="Brokerage charges and external charges need to be lower for equity delivery.",
            rating=4,
            product_area=__import__(
                "groww_intel.schemas", fromlist=["ProductArea"]
            ).ProductArea.STOCKS_DELIVERY,
            source_url="https://example.invalid/r2",
        ),
        Review(
            review_id="r3",
            date=date(2026, 10, 5),
            review_text="Their brokerage of 20 per order execution is too much for me to accept.",
            rating=1,
            product_area=__import__(
                "groww_intel.schemas", fromlist=["ProductArea"]
            ).ProductArea.STOCKS_DELIVERY,
            source_url="https://example.invalid/r3",
        ),
    ]


@pytest.fixture()
def unrelated_reviews() -> list[Review]:
    from groww_intel.schemas import ProductArea

    return [
        Review(
            review_id="u1",
            date=date(2026, 9, 12),
            review_text="The app is very slow to open and the chart takes ages to load.",
            rating=2,
            product_area=ProductArea.PLATFORM,
        ),
        Review(
            review_id="u2",
            date=date(2026, 9, 18),
            review_text="Support did not call back for three days after I raised a complaint.",
            rating=2,
            product_area=ProductArea.PLATFORM,
        ),
    ]


@pytest.fixture()
def offline_llm() -> LLMClient:
    """An LLM client that has no credentials (deterministic mode)."""
    return LLMClient(config=_no_credentials())


def _no_credentials():
    from groww_intel.config import LLMConfig

    return LLMConfig(provider="none")
