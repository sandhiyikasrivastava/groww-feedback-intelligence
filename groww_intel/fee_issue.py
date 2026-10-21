"""Fee-issue investigation.

The issue is *discovered* from the reviews: fee language is detected with a
documented lexicon, grouped per product area (fees from different product
areas are never mixed), and scored against a transparent evidence threshold.

Evidence threshold (all conditions must hold for a candidate to be reported
as a *recurring* issue):

1. ``supporting_reviews >= 3``      – more than an isolated complaint
2. ``explicit_mentions >= 2``       – the reviews name a fee/charge, not just
                                      "support was slow"
3. ``distinct_weeks >= 2``          – the issue recurs over time
4. ``distinct_authors >= 2``        – not the same person repeating themselves
5. ``single product area``          – the reviews sit in one Groww product area;
                                      cross-area reviews are reported separately
                                      instead of being merged in

If no candidate satisfies the threshold the module reports the limitation
instead of inventing a finding.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Iterable, Optional

from .config import get_settings
from .schemas import (
    AnalysisMode,
    FeeCategory,
    FeeIssueCandidate,
    FeeIssueResult,
    ProductArea,
    Review,
    ThemeEvidence,
)
from .analysis import extract_quote

# --------------------------------------------------------------------------
# Fee lexicon
# --------------------------------------------------------------------------

#: Category -> phrases that explicitly name that kind of charge.
FEE_SIGNALS: dict[FeeCategory, tuple[str, ...]] = {
    FeeCategory.BROKERAGE: (
        "brokerage", "per executed order", "per order", "order value",
        "0.1%", "20/-", "flat 5", "flat rs 5", "flat ₹5", "flat 5 rs",
        "brokerage charge", "brokerage charges", "trading charges",
    ),
    FeeCategory.DP_CHARGES: (
        "dp charge", "dp charges", "depository participant", "depository charge",
        "per scrip", "16.5", "dp id",
    ),
    FeeCategory.STATUTORY: (
        "stt", "securities transaction tax", "stamp duty", "transaction charge",
        "transaction charges", "exchange transaction", "exchange charge",
        "turnover charge", "sebi turnover", "statutory charge", "statutory",
        "ipft", "investor protection",
    ),
    FeeCategory.GST: ("gst", "18%", "goods and services tax"),
    FeeCategory.PLATFORM: (
        "platform fee", "platform charges", "subscription", "membership fee",
        "annual charge", "annual charges", "account maintenance", "account charges",
    ),
    FeeCategory.DEMAT_OTHER: (
        "demat charge", "demat charges", "remat", "certification charge",
        "courier charge", "failed demat", "ddpi", "kyc modification",
        "dematerialisation", "dematerialization",
    ),
    FeeCategory.MTF: ("mtf", "margin interest", "interest charge", "interest charges"),
    FeeCategory.MUTUAL_FUND: (
        "exit load", "expense ratio", "amc charge", "mf charge", "direct plan charge",
    ),
    FeeCategory.OTHER: (
        "extra charge", "extra debit", "extra money", "hidden charge",
        "hidden charges", "money deducted", "amount deducted", "unexplained debit",
        "charges applied", "many charges", "high charges", "charges applied",
    ),
}

#: Phrases that only say "there is a charge" without naming the fee.
GENERIC_CHARGE_TERMS = (
    "charge", "charges", "charged", "fee", "fees", "cost", "costs",
)

#: Specific "charge signatures" used to name the recurring confusion.
CHARGE_SIGNATURES: tuple[tuple[str, str], ...] = (
    (r"flat\s*(?:rs\.?|₹)?\s*5\b", "a flat ₹5 charge"),
    (r"per order|per executed order|every order", "a charge levied per order"),
    (r"brokerage", "brokerage"),
    (r"dp charges?", "DP (depository) charges"),
    (r"transaction charges?", "transaction charges"),
    (r"extra (?:charge|debit|money)|unexpected (?:charge|debit)", "an unexpected extra debit"),
    (r"charges? (?:are|is|were|was)?\s*(?:very |too |so )?(?:high|expensive|too much)", "charges perceived as too high"),
    (r"high charges?", "charges perceived as too high"),
    (r"many charges", "many charges applied"),
    (r"charges? applied", "charges applied on transactions"),
    (r"deduct(?:ed|ion)?|debit(?:ed)?", "money deducted from the account"),
    (r"exit load", "mutual fund exit load"),
    (r"gst", "GST on charges"),
    (r"stamp duty", "stamp duty"),
    (r"no (?:annual )?charges", "expectation of zero charges"),
    (r"hidden", "charges described as hidden"),
)

MIN_QUOTE_CHARS = 40

#: Fee-specific product-area rules.  A review that talks about charges is
#: classified with these rules (in order) instead of the generic rules, so
#: that "charges" mentioned in an oil-futures review is not pooled with
#: "charges" mentioned in an equity-delivery review.
FEE_AREA_RULES: tuple[tuple[ProductArea, str], ...] = (
    (ProductArea.COMMODITIES, r"\bcrude\b|\bmcx\b|commodit|\bgold futures?\b|\bsilver futures?\b|\bcrude oil\b"),
    (ProductArea.FUTURES_OPTIONS, r"\bF&O\b|\bfutures?\b|\boptions?\b|\bexpir(y|ies)\b|\bstrike\b|\bCE\b|\bPE\b|shorting|option chain"),
    (ProductArea.MUTUAL_FUNDS, r"\bmutual funds?\b|\bSIP\b|\belss\b|exit load|expense ratio|direct plan|\bAMC\b"),
    (ProductArea.DEMAT_ACCOUNT, r"\bdemat\b|\bdp charges?\b|depository|demateriali"),
    (ProductArea.MTF, r"\bMTF\b|\bmargin\b"),
    (ProductArea.PAYMENTS, r"\bwithdraw|\brefund\b|\bUPI\b|not credited|money stuck"),
    (ProductArea.STOCKS_INTRADAY, r"\bintraday\b|\bBTST\b"),
    (ProductArea.STOCKS_DELIVERY, r"\bstocks?\b|\bshares?\b|\bequity\b|\bdelivery\b|\bholdings?\b|\bportfolio\b|\bsell\b|\bbuy\b"),
)


def fee_context_area(review: Review) -> ProductArea:
    """Classify the product area a fee complaint is about.

    Falls back to the review's stored product area when no fee-specific
    product term is present.
    """
    haystack = _haystack(review)
    for area, pattern in FEE_AREA_RULES:
        if re.search(pattern, haystack):
            return area
    return review.product_area



def _haystack(review: Review) -> str:
    return f"{review.title or ''} {review.review_text}".lower()


def detect_fee_signals(review: Review) -> set[FeeCategory]:
    """Return the fee categories explicitly named by a review."""
    haystack = _haystack(review)
    found: set[FeeCategory] = set()
    for category, phrases in FEE_SIGNALS.items():
        if any(phrase in haystack for phrase in phrases):
            found.add(category)
    return found


def mentions_generic_charge(review: Review) -> bool:
    haystack = _haystack(review)
    return any(term in haystack for term in GENERIC_CHARGE_TERMS)


def charge_signatures(review: Review) -> list[str]:
    """Human-readable signatures of the fee confusion in a review."""
    haystack = _haystack(review)
    signatures: list[str] = []
    for pattern, label in CHARGE_SIGNATURES:
        if re.search(pattern, haystack):
            signatures.append(label)
    return signatures


def _week_key(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _quote_focused(review: Review, needles: Iterable[str]) -> Optional[str]:
    """Return a verbatim window of the review around the first fee phrase."""
    text = " ".join(review.review_text.split())
    lowered = text.lower()
    best_position = None
    for needle in needles:
        position = lowered.find(needle.lower())
        if position != -1 and (best_position is None or position < best_position):
            best_position = position
    if best_position is None:
        return None
    start = max(0, best_position - 90)
    end = min(len(text), best_position + 230)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = snippet.split(" ", 1)[-1] if " " in snippet else snippet
    if end < len(text):
        snippet = snippet.rsplit(" ", 1)[0] if " " in snippet else snippet
    return snippet or None


def _build_candidate(
    issue_id: str,
    category: FeeCategory,
    area: ProductArea,
    reviews: list[Review],
    cross_area_ids: list[str],
    related_unclassified_ids: list[str],
    settings,
) -> FeeIssueCandidate:
    ids = [r.review_id for r in reviews]
    explicit = sum(1 for r in reviews if detect_fee_signals(r))
    weeks = {_week_key(r.date) for r in reviews}
    authors = {(r.author or r.review_id).strip().lower() for r in reviews}
    ratings = [r.rating for r in reviews if r.rating is not None]
    low_share = (
        sum(1 for r in ratings if r <= 2) / len(ratings) if ratings else 0.0
    )

    signature_counter: Counter[str] = Counter()
    for review in reviews:
        for signature in charge_signatures(review):
            signature_counter[signature] += 1
    dominant_signatures = [s for s, _ in signature_counter.most_common(3)]
    label = f"{category.value} — {area.value}"
    if dominant_signatures:
        label += f" ({'; '.join(dominant_signatures)})"

    quotes: list[ThemeEvidence] = []
    signal_phrases = [s for category_signals in FEE_SIGNALS.values() for s in category_signals]
    ordered = sorted(
        reviews,
        key=lambda r: (
            -sum(1 for p in signal_phrases if p in _haystack(r)),
            r.rating if r.rating is not None else 5.0,
            r.date,
        ),
    )
    for review in ordered:
        quote = _quote_focused(review, signal_phrases) or extract_quote(
            review.review_text, min_chars=MIN_QUOTE_CHARS
        )
        if not quote:
            continue
        quotes.append(
            ThemeEvidence(
                review_id=review.review_id,
                date=review.date,
                rating=review.rating,
                product_area=review.product_area,
                excerpt=quote,
                source_url=review.source_url,
            )
        )
        if len(quotes) >= 3:
            break

    threshold_detail = {
        "supporting_reviews": {
            "value": len(reviews),
            "required": settings.fee_min_supporting_reviews,
            "passed": len(reviews) >= settings.fee_min_supporting_reviews,
        },
        "explicit_fee_mentions": {
            "value": explicit,
            "required": settings.fee_min_explicit_mentions,
            "passed": explicit >= settings.fee_min_explicit_mentions,
        },
        "distinct_weeks": {
            "value": len(weeks),
            "required": settings.fee_min_distinct_weeks,
            "passed": len(weeks) >= settings.fee_min_distinct_weeks,
        },
        "distinct_authors": {
            "value": len(authors),
            "required": 2,
            "passed": len(authors) >= 2,
        },
        "single_product_area": {
            "value": f"{area.value} ({len(reviews)} reviews)",
            "required": "reviews of this issue must sit in one product area",
            "passed": area is not ProductArea.UNKNOWN,
            "cross_area_reviews_excluded": len(cross_area_ids),
            "area_not_stated_reviews_excluded": len(related_unclassified_ids),
        },
    }
    meets = all(entry["passed"] for entry in threshold_detail.values())

    return FeeIssueCandidate(
        issue_id=issue_id,
        label=label,
        fee_category=category,
        product_area=area,
        supporting_review_ids=ids,
        explicit_mentions=explicit,
        distinct_weeks=len(weeks),
        distinct_authors=len(authors),
        mean_rating=(sum(ratings) / len(ratings)) if ratings else None,
        low_rating_share=low_share,
        quotes=quotes,
        other_area_review_ids=cross_area_ids,
        related_unclassified_review_ids=related_unclassified_ids,
        area_stated=area is not ProductArea.UNKNOWN,
        meets_threshold=meets,
        threshold_detail=threshold_detail,
        interpretation=(
            f"{len(reviews)} reviews in {area.value} name this fee; the most common "
            f"phrasing is '{dominant_signatures[0]}'." if dominant_signatures else
            f"{len(reviews)} reviews in {area.value} name this fee."
        ),
    )


def investigate_fee_issue(
    reviews: list[Review],
    *,
    window_end: Optional[date] = None,
) -> FeeIssueResult:
    """Discover the most significant recurring fee issue in the review set."""
    settings = get_settings()
    window_end = window_end or date.today()

    fee_reviews = [r for r in reviews if detect_fee_signals(r) or mentions_generic_charge(r)]
    if not fee_reviews:
        return FeeIssueResult(
            mode=AnalysisMode.HEURISTIC,
            insufficient_evidence=True,
            limitation_note=(
                "No review in the analysed window mentions a fee, charge or deduction, "
                "so no fee issue could be investigated. This is a data limitation, not a "
                "finding that Groww's pricing is clear."
            ),
            evidence_threshold=_threshold_description(settings),
        )

    by_category_area: dict[tuple[FeeCategory, ProductArea], list[Review]] = defaultdict(list)
    area_by_review: dict[str, ProductArea] = {}
    category_by_review: dict[str, set[FeeCategory]] = {}
    for review in fee_reviews:
        categories = detect_fee_signals(review) or {FeeCategory.OTHER}
        category_by_review[review.review_id] = categories
        area = fee_context_area(review)
        area_by_review[review.review_id] = area
        for category in categories:
            by_category_area[(category, area)].append(review)

    candidates: list[FeeIssueCandidate] = []
    for index, ((category, area), group) in enumerate(sorted(by_category_area.items()), start=1):
        unique = {r.review_id: r for r in group}
        group_reviews = list(unique.values())
        cross_area = [
            r.review_id
            for r in fee_reviews
            if r.review_id not in unique
            and category in category_by_review.get(r.review_id, set())
            and area_by_review.get(r.review_id) != area
            and area_by_review.get(r.review_id) is not ProductArea.UNKNOWN
        ]
        related_unclassified = [
            r.review_id
            for r in fee_reviews
            if r.review_id not in unique
            and category in category_by_review.get(r.review_id, set())
            and area_by_review.get(r.review_id) is ProductArea.UNKNOWN
        ]
        candidate = _build_candidate(
            f"fee-{index}",
            category,
            area,
            group_reviews,
            cross_area,
            related_unclassified,
            settings,
        )
        candidates.append(candidate)

    # Only a candidate whose product area is actually stated can become the
    # reported finding — otherwise we could not say which product the fee
    # confusion belongs to.
    qualifying = [
        c for c in candidates if c.meets_threshold and c.area_stated
    ]
    selected: Optional[FeeIssueCandidate] = None
    if qualifying:
        qualifying.sort(
            key=lambda c: (
                len(c.supporting_review_ids),
                c.explicit_mentions,
                c.low_rating_share,
                c.distinct_weeks,
            ),
            reverse=True,
        )
        selected = qualifying[0]

    limitation = ""
    if selected is None:
        limitation = (
            "Fee language appears in the review set, but no single fee/product-area "
            f"combination reached the evidence threshold (>= {settings.fee_min_supporting_reviews} "
            "supporting reviews, >= 2 explicit fee mentions, >= 2 distinct weeks, >= 2 "
            "distinct authors, and a product area that the reviews actually state). "
            "The candidate list below shows the closest matches; no fee finding is asserted."
        )

    return FeeIssueResult(
        mode=AnalysisMode.HEURISTIC,
        selected=selected,
        candidates=sorted(
            candidates, key=lambda c: (-len(c.supporting_review_ids), -c.explicit_mentions)
        ),
        insufficient_evidence=selected is None,
        limitation_note=limitation,
        evidence_threshold=_threshold_description(settings),
    )


def _threshold_description(settings) -> dict:
    return {
        "supporting_reviews": f">= {settings.fee_min_supporting_reviews}",
        "explicit_fee_mentions": f">= {settings.fee_min_explicit_mentions}",
        "distinct_weeks": f">= {settings.fee_min_distinct_weeks}",
        "distinct_authors": ">= 2",
        "single_product_area": "required (reviews are never merged across areas)",
        "note": (
            "A candidate must satisfy every condition. Candidates that fail are listed "
            "but are not presented as findings."
        ),
    }
