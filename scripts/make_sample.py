"""Create the bundled sample dataset.

The sample is a **real** subset of the collected Apple App Store reviews —
not synthetic text.  It is small enough to read by hand and includes the
charge-related reviews so the fee-issue investigation can be demonstrated.

    python scripts/make_sample.py
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from groww_intel.fee_issue import detect_fee_signals, mentions_generic_charge  # noqa: E402
from groww_intel.ingestion import read_reviews_csv  # noqa: E402

COLLECTED = PROJECT_ROOT / "data" / "collected" / "groww_appstore_reviews.csv"
OUT_DIR = PROJECT_ROOT / "data" / "sample"
OUT_CSV = OUT_DIR / "groww_reviews_sample.csv"


def main() -> int:
    if not COLLECTED.exists():
        print(f"Collected dataset not found: {COLLECTED}", file=sys.stderr)
        return 1

    ingestion = read_reviews_csv(COLLECTED, as_of=date(2026, 10, 21), weeks=12)
    reviews = ingestion.reviews

    fee_reviews = [
        r
        for r in reviews
        if detect_fee_signals(r) or mentions_generic_charge(r)
    ]
    others = [r for r in reviews if r not in fee_reviews]

    # Keep every charge-related review plus a spread of other themes.
    selected = fee_reviews[:18] + others[:12]
    selected.sort(key=lambda r: (r.date, r.review_id))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "review_id", "date", "title", "review_text", "rating", "product_area",
        "product_area_source", "platform", "source", "source_url", "author",
        "app_version", "collected_at", "collection_feed",
    ]
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for review in selected:
            writer.writerow(
                {
                    "review_id": review.review_id,
                    "date": review.date.isoformat(),
                    "title": review.title or "",
                    "review_text": review.review_text,
                    "rating": review.rating if review.rating is not None else "",
                    "product_area": review.product_area.value,
                    "product_area_source": review.product_area_source,
                    "platform": review.platform.value,
                    "source": review.source or "",
                    "source_url": review.source_url or "",
                    "author": review.author or "",
                    "app_version": review.app_version or "",
                    "collected_at": datetime_now(),
                    "collection_feed": "mostrecent/mosthelpful",
                }
            )

    readme = OUT_DIR / "README.md"
    readme.write_text(
        "# Bundled sample dataset\n\n"
        "`groww_reviews_sample.csv` is a **real** subset of the collected Apple App Store\n"
        "customer reviews for the Groww app (India storefront) — it is *not* synthetic or\n"
        "AI-written text. It contains "
        f"{len(selected)} verbatim reviews from the {ingestion.report.date_range_start} → "
        f"{ingestion.report.date_range_end} window, including the charge-related reviews that\n"
        "the fee-issue investigation uses.\n\n"
        "The full collected dataset lives in `../collected/groww_appstore_reviews.csv` and is\n"
        "reproduced by `python scripts/collect_appstore_reviews.py`.\n\n"
        "Every review keeps its original App Store review id and the public review-page link\n"
        "so each finding can be traced back to the source.\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT_CSV} ({len(selected)} reviews)")
    return 0


def datetime_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
