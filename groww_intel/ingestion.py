"""CSV ingestion and normalisation.

Responsibilities
----------------
* read a CSV of customer reviews with arbitrary column naming,
* map columns onto the canonical schema (see ``COLUMN_ALIASES``),
* normalise dates, ratings, text and product areas,
* de-duplicate exact repeats,
* apply the recency window filter.

Nothing in this module talks to a network or an LLM.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from .config import get_settings
from .schemas import (
    ExcludedRecord,
    Platform,
    ProductArea,
    Review,
    ValidationReport,
)

# --------------------------------------------------------------------------
# Column normalisation
# --------------------------------------------------------------------------

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "review_id": (
        "review_id", "reviewid", "id", "review id", "comment_id", "commentid",
        "feedback_id", "review_identifier", "identifier",
    ),
    "date": (
        "date", "review_date", "reviewdate", "created_at", "createdat",
        "date_posted", "posted_date", "datetime", "timestamp", "posted",
        "review posted", "reviewdateposted",
    ),
    "review_text": (
        "review_text", "reviewtext", "text", "review", "body", "content",
        "comment", "feedback", "review_body", "description", "customer_review",
        "review content", "body text",
    ),
    "rating": (
        "rating", "star", "stars", "score", "rate", "ratings", "review_rating",
        "star_rating", "rating_out_of_5",
    ),
    "product_area": (
        "product_area", "productarea", "category", "product", "area", "segment",
        "vertical", "topic", "product category",
    ),
    "platform": (
        "platform", "source_platform", "device", "channel", "os", "store",
        "app_platform", "platform name",
    ),
    "source": (
        "source", "source_name", "origin", "feedback_source", "source name",
    ),
    "source_url": (
        "source_url", "sourceurl", "url", "link", "review_url", "permalink",
        "source link", "review link",
    ),
    "title": ("title", "review_title", "headline", "summary", "review heading"),
    "author": (
        "author", "reviewer", "customer", "user", "author_name", "name",
        "reviewer_name", "reviewer id",
    ),
    "app_version": ("app_version", "version", "appversion", "app version"),
    "collected_at": (
        "collected_at", "collectedat", "scraped_at", "fetched_at", "retrieved_at",
    ),
}

#: Rows are rejected when the review text is missing or shorter than this.
MIN_TEXT_LENGTH = 3

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
)

#: Product-area inference rules, evaluated in order (first match wins).
#: The result is flagged as ``derived_heuristic`` so the UI can distinguish it
#: from a product area that was supplied by the data source.
AREA_RULES: tuple[tuple[ProductArea, str], ...] = (
    (ProductArea.MUTUAL_FUNDS, r"\bmutual funds?\b|\bSIP\b|\bSIPs\b|\belss\b|\bNFO\b|\bAMC\b|exit load|expense ratio|direct plan|flex ?small cap|liquid fund|index fund|\bMF\b"),
    (ProductArea.FUTURES_OPTIONS, r"\bF&O\b|\bfutures?\b|option chain|\boptions?\b|\bcall\b|\bput\b|expir(y|ies)|strike price|option buying|shorting|\bCE\b|\bPE\b|intraday option"),
    (ProductArea.COMMODITIES, r"\bcrude\b|\bgold\b|\bsilver\b|commodit|\bMCX\b|\bmcx\b"),
    (ProductArea.MTF, r"\bMTF\b|\bmargin\b"),
    (ProductArea.DEMAT_ACCOUNT, r"\bdemat\b|depository|\bdp id\b|\bdp charge"),
    (ProductArea.IPO, r"\bIPO\b|allotment|\bapply\b.*\bIPO\b"),
    (ProductArea.CREDIT, r"\bcredit\b|\bloan\b|\bEMI\b|personal loan|loan against"),
    (ProductArea.PAYMENTS, r"\bUPI\b|wallet|groww pay|withdraw|\brefund\b|bank transfer|\bNEFT\b|payment failed|payment pending|money (?:is )?not credited"),
    (ProductArea.STOCKS_INTRADAY, r"\bintraday\b|\bBTST\b|\bMIS\b|same day"),
    (ProductArea.STOCKS_DELIVERY, r"\bstocks?\b|\bshares?\b|delivery|\bequity\b|\border\b|\bswing\b|\btarget\b|\bholdings?\b|\bportfolio\b"),
    (ProductArea.PLATFORM, r"\bapp\b|\bui\b|interface|login|sign ?in|slow|bug|crash|\bupdate\b|\botp\b|otp|verification|\blogin\b"),
)

_PLATFORM_RULES: tuple[tuple[Platform, str], ...] = (
    (Platform.IOS_APP_STORE, r"ios|itunes|app ?store|apple"),
    (Platform.ANDROID_PLAY_STORE, r"android|play ?store|google ?play"),
    (Platform.WEB, r"\bweb\b|browser"),
)


class IngestionError(Exception):
    """Raised when a CSV cannot be read at all."""


@dataclass
class IngestionResult:
    """Reviews plus the validation report describing what happened."""

    reviews: list[Review] = field(default_factory=list)
    report: ValidationReport = field(default_factory=lambda: ValidationReport(file_name=""))
    normalized_rows: pd.DataFrame = field(default_factory=pd.DataFrame)


def _norm_key(name: Any) -> str:
    text = str(name or "").strip().lower()
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def build_column_map(columns: Iterable[Any]) -> dict[str, str]:
    """Map canonical field -> actual CSV column name."""
    available = {(_norm_key(c)): c for c in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in available:
                mapping[canonical] = str(available[alias])
                break
    return mapping


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        # epoch seconds / milliseconds
        seconds = float(value)
        if seconds > 1e11:
            seconds /= 1000.0
        try:
            return datetime.utcfromtimestamp(seconds).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "nat"}:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:  # last resort: pandas inference
        parsed = pd.to_datetime(text, errors="raise", dayfirst=True)
        if isinstance(parsed, pd.Timestamp):
            return parsed.date()
    except Exception:
        pass
    return None


def _parse_rating(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        rating = float(value)
    else:
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none", "null"}:
            return None
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        rating = float(match.group(0))
    if rating < 1 or rating > 5:
        return None
    return rating


def infer_product_area(text: str) -> ProductArea:
    """Derive a product area from review text using documented rules."""
    lowered = text.lower()
    for area, pattern in AREA_RULES:
        if re.search(pattern, lowered):
            return area
    return ProductArea.UNKNOWN


def infer_platform(text: str) -> Platform:
    lowered = text.lower()
    for platform, pattern in _PLATFORM_RULES:
        if re.search(pattern, lowered):
            return platform
    return Platform.UNKNOWN


def _normalise_product_area(value: Any) -> Optional[ProductArea]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    for area in ProductArea:
        if text.lower() == area.value.lower() or text.lower() == area.name.lower():
            return area
    # tolerate snake/kebab variants e.g. "stocks_delivery"
    slug = _norm_key(text)
    for area in ProductArea:
        if _norm_key(area.value) == slug or _norm_key(area.name) == slug:
            return area
    lowered = text.lower()
    for area, pattern in AREA_RULES:
        if re.search(pattern, lowered):
            return area
    return None


def _normalise_platform(value: Any) -> Optional[Platform]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    for platform in Platform:
        if text.lower() == platform.value.lower() or text.lower() == platform.name.lower():
            return platform
    slug = _norm_key(text)
    for platform in Platform:
        if _norm_key(platform.value) == slug or _norm_key(platform.name) == slug:
            return platform
    lowered = text.lower()
    for platform, pattern in _PLATFORM_RULES:
        if re.search(pattern, lowered):
            return platform
    return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u200b", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _dedupe_key(review: Review) -> str:
    """Exact-duplicate key: same text + same day + same rating + same author."""
    payload = "|".join(
        [
            review.review_text.lower(),
            review.date.isoformat(),
            "" if review.rating is None else f"{review.rating:g}",
            (review.author or "").strip().lower(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_reviews_csv(
    path: str | Path,
    *,
    as_of: Optional[date] = None,
    weeks: Optional[int] = None,
) -> IngestionResult:
    """Read, normalise, validate and window-filter a review CSV.

    Parameters
    ----------
    path:
        CSV file containing the reviews.
    as_of:
        Last date of the analysis window (defaults to today).
    weeks:
        Size of the recency window in weeks (8–12 by default).
    """
    settings = get_settings()
    weeks = weeks or settings.default_weeks
    weeks = max(settings.weeks_min, min(settings.weeks_max, int(weeks)))
    as_of = as_of or date.today()
    window_start = as_of - timedelta(weeks=weeks)

    csv_path = Path(path)
    if not csv_path.exists():
        raise IngestionError(f"File not found: {csv_path}")
    try:
        raw_df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except Exception as exc:  # pragma: no cover - defensive
        raise IngestionError(f"Could not read CSV: {exc}") from exc

    report = ValidationReport(
        file_name=csv_path.name,
        window_start=window_start,
        window_end=as_of,
    )
    column_map = build_column_map(raw_df.columns)
    report.column_map = column_map

    if "review_text" not in column_map:
        raise IngestionError(
            "Could not find a review-text column. Expected one of: "
            + ", ".join(COLUMN_ALIASES["review_text"][:8])
        )
    if "date" not in column_map:
        raise IngestionError(
            "Could not find a date column. Expected one of: "
            + ", ".join(COLUMN_ALIASES["date"][:8])
        )

    reviews: list[Review] = []
    seen: dict[str, str] = {}
    missing_id_counter = 0

    for position, row in enumerate(raw_df.to_dict(orient="records"), start=2):
        report.rows_read += 1
        raw_text = _clean_text(row.get(column_map["review_text"]))
        if len(raw_text) < MIN_TEXT_LENGTH:
            report.excluded += 1
            report.excluded_records.append(
                ExcludedRecord(
                    row_number=position,
                    reason_code="empty_review_text",
                    reason="Review text missing or shorter than the minimum length.",
                    raw={k: v for k, v in row.items() if v not in ("", None)},
                )
            )
            continue

        parsed_date = _parse_date(row.get(column_map["date"]))
        if parsed_date is None:
            report.excluded += 1
            report.excluded_records.append(
                ExcludedRecord(
                    row_number=position,
                    review_id=str(row.get(column_map.get("review_id", ""), "")) or None,
                    reason_code="unparseable_date",
                    reason="Date could not be parsed into a calendar date.",
                    raw={k: v for k, v in row.items() if v not in ("", None)},
                )
            )
            continue

        if not (window_start <= parsed_date <= as_of):
            report.excluded += 1
            report.excluded_records.append(
                ExcludedRecord(
                    row_number=position,
                    reason_code="outside_recency_window",
                    reason=(
                        f"Review dated {parsed_date.isoformat()} is outside the "
                        f"{weeks}-week window ({window_start.isoformat()} → {as_of.isoformat()})."
                    ),
                    raw={k: v for k, v in row.items() if v not in ("", None)},
                )
            )
            continue

        rating = _parse_rating(row.get(column_map.get("rating", "")))
        if column_map.get("rating") and row.get(column_map["rating"], "") not in ("", None) and rating is None:
            report.warnings.append(
                f"Row {position}: rating '{row[column_map['rating']]}' is out of the 1-5 range and was dropped."
            )

        source_area = None
        if column_map.get("product_area"):
            source_area = _normalise_product_area(row.get(column_map["product_area"]))
        if source_area is not None:
            area, area_source = source_area, "source"
        else:
            area, area_source = infer_product_area(raw_text), "derived_heuristic"
            if area is ProductArea.UNKNOWN:
                area_source = "unknown"

        source_platform = None
        if column_map.get("platform"):
            source_platform = _normalise_platform(row.get(column_map["platform"]))
        if source_platform is not None:
            platform = source_platform
        else:
            platform = infer_platform(raw_text)

        title = _clean_text(row.get(column_map.get("title", ""))) or None
        author = _clean_text(row.get(column_map.get("author", ""))) or None
        source_url = _clean_text(row.get(column_map.get("source_url", ""))) or None
        source_name = _clean_text(row.get(column_map.get("source", ""))) or None
        app_version = _clean_text(row.get(column_map.get("app_version", ""))) or None
        collected_at = _clean_text(row.get(column_map.get("collected_at", ""))) or None

        review_id = _clean_text(row.get(column_map.get("review_id", "")))
        if not review_id:
            missing_id_counter += 1
            review_id = f"row-{position}"
            report.warnings.append(f"Row {position}: missing review id, assigned '{review_id}'.")

        try:
            review = Review(
                review_id=review_id,
                date=parsed_date,
                review_text=raw_text,
                rating=rating,
                product_area=area,
                product_area_source=area_source,
                platform=platform,
                source=source_name,
                source_url=source_url,
                title=title,
                author=author,
                app_version=app_version,
            )
        except Exception as exc:
            report.excluded += 1
            report.excluded_records.append(
                ExcludedRecord(
                    row_number=position,
                    review_id=review_id,
                    reason_code="schema_validation_failed",
                    reason=str(exc),
                    raw={k: v for k, v in row.items() if v not in ("", None)},
                )
            )
            continue

        key = _dedupe_key(review)
        if key in seen:
            report.duplicates_removed += 1
            report.deduplicated += 1
            report.excluded_records.append(
                ExcludedRecord(
                    row_number=position,
                    review_id=review_id,
                    reason_code="exact_duplicate",
                    reason=f"Exact duplicate of review '{seen[key]}' (same text, date, rating and author).",
                )
            )
            continue
        seen[key] = review_id
        reviews.append(review)

    reviews.sort(key=lambda r: (r.date, r.review_id))
    report.accepted = len(reviews)
    report.excluded = len(report.excluded_records)
    if reviews:
        report.date_range_start = min(r.date for r in reviews)
        report.date_range_end = max(r.date for r in reviews)
    report.warnings = list(dict.fromkeys(report.warnings))

    normalized = pd.DataFrame(
        [
            {
                "review_id": r.review_id,
                "date": r.date.isoformat(),
                "review_text": r.review_text,
                "rating": r.rating,
                "product_area": r.product_area.value,
                "product_area_source": r.product_area_source,
                "platform": r.platform.value,
                "source": r.source,
                "source_url": r.source_url,
                "title": r.title,
                "author": r.author,
                "app_version": r.app_version,
            }
            for r in reviews
        ]
    )

    return IngestionResult(reviews=reviews, report=report, normalized_rows=normalized)
