"""Theme analysis over the validated review set.

Two modes:

``llm``
    A map/reduce pipeline: the reviews are chunked, each chunk is asked for
    candidate themes, and the candidates are consolidated into at most five
    themes.  Every theme is then re-validated against the dataset — a review
    id that does not exist in the dataset is dropped, and counts/severity are
    always *recomputed* from the dataset rather than trusted from the model.

``heuristic``
    Used when no LLM credentials are configured.  Themes come from a fixed,
    documented keyword taxonomy.  The UI labels the mode so the distinction
    between "measured fact" and "AI interpretation" stays visible.

Measured facts vs interpretation
--------------------------------
* measured  : review counts, ratings, dates, text (all recomputed here)
* interpreted: theme names, summaries and the meaning assigned to a theme
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import mean, median
from typing import Iterable, Optional

from .config import get_settings
from .llm import LLMClient, LLMError
from .schemas import (
    AnalysisMode,
    AnalysisResult,
    Review,
    Theme,
    ThemeEvidence,
    ThemeRanking,
)

# --------------------------------------------------------------------------
# Heuristic taxonomy (also used as a fallback labelling hint for the LLM)
# --------------------------------------------------------------------------

HEURISTIC_THEMES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "charges",
        "Charges, fees and transaction costs",
        (
            "charge", "charges", "brokerage", "gst", "dp charge", "dp charges",
            "demat charge", "transaction charge", "stamp duty", "exit load",
            "expense ratio", "amc", "fee", "fees", "deduct", "debited",
            "extra debit", "hidden charge", "per order", "platform fee",
        ),
    ),
    (
        "support",
        "Customer support responsiveness",
        (
            "support", "customer care", "customer service", "helpline",
            "call back", "callback", "no reply", "not responding", "ticket",
            "chat support", "raise a ticket", "agent", "complaint escalated",
        ),
    ),
    (
        "app_stability",
        "App stability, speed and interface",
        (
            "slow", "sluggish", "lag", "lagging", "crash", "crashed", "bug",
            "bugs", "glitch", "freeze", "froze", "hang", "loading", "ui",
            "interface", "user interface", "layout", "dark mode",
        ),
    ),
    (
        "onboarding",
        "Account opening, KYC and demat activation",
        (
            "kyc", "rekyc", "onboarding", "demat account", "open demat",
            "account opening", "activation", "activated", "verification",
            "pan card", "aadhaar", "account blocked", "account frozen",
        ),
    ),
    (
        "payments",
        "Withdrawals, refunds and money movement",
        (
            "withdraw", "withdrawal", "withdrawl", "refund", "payout",
            "money transfer", "money not credited", "not credited", "upi",
            "bank account", "gift card", "money stuck",
        ),
    ),
    (
        "features",
        "Feature requests and product experience",
        (
            "feature request", "would be good", "please add", "suggest",
            "suggestion", "add an option", "add option", "indicator",
            "chart", "watchlist", "screener", "ipo alert", "wishlist",
        ),
    ),
)

#: Weighting used to rank themes.  Documented in the README and shown in the UI.
RANKING_WEIGHTS = {
    "frequency": 0.35,
    "recency": 0.20,
    "severity": 0.25,
    "rating": 0.10,
    "evidence_quality": 0.10,
}

MAX_EXCERPT_CHARS = 320


def _excerpt(text: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut + "…"


def review_digest_line(review: Review) -> str:
    """One-line representation of a review for the LLM prompt."""
    rating = "n/a" if review.rating is None else f"{review.rating:g}"
    return (
        f"[{review.review_id}] {review.date.isoformat()} | rating={rating} | "
        f"area={review.product_area.value} | title={review.title or '-'} | "
        f"text={_excerpt(review.review_text, 420)}"
    )


def build_evidence(review: Review) -> ThemeEvidence:
    return ThemeEvidence(
        review_id=review.review_id,
        date=review.date,
        rating=review.rating,
        product_area=review.product_area,
        excerpt=_excerpt(review.review_text),
        source_url=review.source_url,
    )


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def _theme_dates(theme: Theme) -> list[date]:
    return [e.date for e in theme.evidence]


def _recency_score(theme: Theme, window_end: date) -> float:
    """Share of the theme's reviews that fall in the most recent 4 weeks."""
    if not theme.evidence:
        return 0.0
    cutoff = window_end - timedelta(weeks=4)
    recent = sum(1 for e in theme.evidence if e.date >= cutoff)
    return recent / len(theme.evidence)


def _severity_score(theme: Theme) -> float:
    rated = [e.rating for e in theme.evidence if e.rating is not None]
    if not rated:
        return 0.5  # neutral when ratings are unavailable
    low = sum(1 for r in rated if r <= 2)
    return low / len(rated)


def _rating_score(theme: Theme) -> float:
    rated = [e.rating for e in theme.evidence if e.rating is not None]
    if not rated:
        return 0.5
    return max(0.0, min(1.0, (5.0 - mean(rated)) / 4.0))


def _evidence_quality_score(theme: Theme, reviews_by_id: dict[str, Review]) -> float:
    """Blend of substantive text and keyword corroboration of the assignment."""
    if not theme.evidence:
        return 0.0
    lengths = []
    for evidence in theme.evidence:
        review = reviews_by_id.get(evidence.review_id)
        if review:
            lengths.append(len(review.review_text))
    length_component = min(1.0, (mean(lengths) / 400.0)) if lengths else 0.0
    corroborated = 0
    for evidence in theme.evidence:
        review = reviews_by_id.get(evidence.review_id)
        if not review:
            continue
        haystack = f"{review.title or ''} {review.review_text}".lower()
        if any(kw.lower() in haystack for kw in theme.keywords):
            corroborated += 1
    corroboration_component = corroborated / len(theme.evidence)
    return 0.5 * length_component + 0.5 * corroboration_component


def rank_themes(
    themes: list[Theme],
    reviews_by_id: dict[str, Review],
    window_end: date,
) -> list[ThemeRanking]:
    """Rank themes with the documented criteria; counts are always measured."""
    if not themes:
        return []
    max_count = max(t.measured_count for t in themes) or 1
    scored: list[ThemeRanking] = []
    for theme in themes:
        count = theme.measured_count
        frequency = count / max_count
        recency = _recency_score(theme, window_end)
        severity = _severity_score(theme)
        rating = _rating_score(theme)
        quality = _evidence_quality_score(theme, reviews_by_id)
        total = 100 * (
            RANKING_WEIGHTS["frequency"] * frequency
            + RANKING_WEIGHTS["recency"] * recency
            + RANKING_WEIGHTS["severity"] * severity
            + RANKING_WEIGHTS["rating"] * rating
            + RANKING_WEIGHTS["evidence_quality"] * quality
        )
        scored.append(
            ThemeRanking(
                theme_id=theme.theme_id,
                name=theme.name,
                count=count,
                frequency_score=round(frequency, 4),
                recency_score=round(recency, 4),
                severity_score=round(severity, 4),
                rating_score=round(rating, 4),
                evidence_quality_score=round(quality, 4),
                total_score=round(total, 2),
                rank=0,
                components_explained={
                    "frequency": "review count relative to the largest theme",
                    "recency": "share of the theme's reviews from the last 4 weeks of the window",
                    "severity": "share of the theme's rated reviews scoring 2 or lower",
                    "rating": "mean rating (lower = more severe), normalised",
                    "evidence_quality": "text substance + share of reviews matching the theme keywords",
                },
            )
        )
    scored.sort(key=lambda r: (-r.total_score, -r.count, r.name))
    for index, ranking in enumerate(scored, start=1):
        ranking.rank = index
    return scored


def temporal_comparison(theme: Theme, window_start: date, window_end: date) -> str:
    """Compare the first and second half of the window — only when supported."""
    midpoint = window_start + (window_end - window_start) / 2
    first = [e for e in theme.evidence if e.date < midpoint]
    second = [e for e in theme.evidence if e.date >= midpoint]
    if len(first) < 5 or len(second) < 5:
        return (
            "Not enough reviews in both halves of the window to state a direction "
            f"({len(first)} earlier / {len(second)} later)."
        )
    if len(second) > len(first):
        return f"More reviews in the second half of the window ({len(second)} vs {len(first)} earlier)."
    if len(first) > len(second):
        return f"Fewer reviews in the second half of the window ({len(second)} vs {len(first)} earlier)."
    return f"Even split across the window ({len(second)} vs {len(first)} earlier)."


# --------------------------------------------------------------------------
# Quotation extraction (verbatim only)
# --------------------------------------------------------------------------


def extract_quote(text: str, min_chars: int = 40, max_chars: int = 320) -> Optional[str]:
    """Return a verbatim substring of ``text`` suitable as a quotation.

    The result is always an exact (whitespace-normalised) substring of the
    original review — nothing is reworded.
    """
    cleaned = " ".join(text.split())
    if len(cleaned) < min_chars:
        return None
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    quote = ""
    for sentence in sentences:
        candidate = (quote + " " + sentence).strip() if quote else sentence.strip()
        if len(candidate) > max_chars and quote:
            break
        quote = candidate
        if len(quote) >= min_chars:
            break
    if len(quote) < min_chars:
        quote = cleaned[:max_chars].rsplit(" ", 1)[0]
    return quote or None


def verify_quotation(quote: str, reviews: Iterable[Review]) -> Optional[Review]:
    """Return the review a quotation came from, or ``None`` if it is not verbatim."""
    needle = " ".join(quote.split()).lower()
    for review in reviews:
        haystack = " ".join(review.review_text.split()).lower()
        if needle and needle in haystack:
            return review
    return None


CONCRETE_TERMS = (
    "charge", "charges", "brokerage", "fee", "gst", "demat", "kyc", "withdraw",
    "refund", "sip", "mutual fund", "option", "futures", "order", "app", "support",
    "login", "payment", "balance", "ipo", "chart",
)


def _specificity_score(review: Review) -> float:
    """How concrete and informative a review is as evidence."""
    text = review.review_text.lower()
    score = 0.0
    if re.search(r"\d", text):
        score += 2.0
    if any(term in text for term in CONCRETE_TERMS):
        score += 1.0
    if 60 <= len(review.review_text) <= 260:
        score += 1.0
    if review.rating is not None and review.rating <= 2:
        score += 1.0
    if review.rating is not None and review.rating >= 5 and len(review.review_text) < 120:
        score -= 1.0  # short praise is not useful evidence
    return score


def select_quotations(
    reviews: list[Review],
    themes: list[Theme],
    limit: int = 3,
) -> list[ThemeEvidence]:
    """Pick up to ``limit`` verbatim quotations from the highest-ranked themes.

    One quotation is taken from each of the leading themes first (so the
    pulse represents more than one issue), then the most concrete remaining
    reviews are used to fill the quota.
    """
    if not reviews:
        return []
    by_id = {r.review_id: r for r in reviews}
    ordered_themes = sorted(themes, key=lambda t: -t.measured_count)
    chosen: list[ThemeEvidence] = []
    used_ids: set[str] = set()

    def add(review: Review) -> bool:
        quote = extract_quote(review.review_text)
        if not quote:
            return False
        chosen.append(
            ThemeEvidence(
                review_id=review.review_id,
                date=review.date,
                rating=review.rating,
                product_area=review.product_area,
                excerpt=quote,
                source_url=review.source_url,
            )
        )
        used_ids.add(review.review_id)
        return True

    # 1) one quote per leading theme, most concrete first
    for theme in ordered_themes:
        if len(chosen) >= limit:
            break
        candidates = [
            by_id[e.review_id]
            for e in theme.evidence
            if e.review_id in by_id and e.review_id not in used_ids
        ]
        candidates.sort(key=lambda r: (-_specificity_score(r), -r.date.toordinal()))
        for review in candidates:
            if add(review):
                break

    # 2) fill the remaining slots with the most concrete unused reviews
    if len(chosen) < limit:
        remaining = [r for r in reviews if r.review_id not in used_ids]
        remaining.sort(key=lambda r: (-_specificity_score(r), -r.date.toordinal()))
        for review in remaining:
            if len(chosen) >= limit:
                break
            add(review)

    return chosen[:limit]


# --------------------------------------------------------------------------
# LLM pipeline
# --------------------------------------------------------------------------

CHUNK_SIZE = 60

CHUNK_SYSTEM = (
    "You are a senior product analyst for an Indian investing platform (Groww). "
    "You read customer app-store reviews and name the themes they describe. "
    "You never invent reviews: every review id you cite must be copied from the input. "
    "You answer with a single JSON object and nothing else."
)

CHUNK_USER = """Below are {n} customer reviews from the Groww app, each on one line as
[id] date | rating=... | area=... | title=... | text=...

{lines}

Propose the candidate themes present in THIS batch only. Rules:
- At most 5 candidate themes per batch.
- A theme name must be a short, specific product-issue label (max 90 characters).
- "keywords": 3-8 lowercase words/phrases that literally appear in the reviews of that theme.
- "review_ids": the ids of the reviews in THIS batch that belong to the theme. Copy ids exactly.
- "summary": one sentence describing what customers actually complain about or praise.
- Only use ids present in the batch.

Return JSON of the form:
{{"themes": [{{"name": "...", "summary": "...", "keywords": ["..."], "review_ids": ["..."]}}]}}"""

CONSOLIDATE_SYSTEM = (
    "You are a senior product analyst for Groww, an Indian investing platform. "
    "You consolidate candidate themes into at most five themes that best explain the "
    "review set. You only keep review ids that were supplied to you. You answer with a "
    "single JSON object and nothing else."
)

CONSOLIDATE_USER = """Candidate themes proposed from {n} batches, covering {m} reviews.

{candidates}

Consolidate them into AT MOST {max_themes} themes (usually 3-5). Rules:
- Merge duplicates (same underlying issue phrased differently); keep the clearest name.
- "review_ids" must be a subset of the ids listed in the candidates above — copy them exactly.
- Do not include a theme with fewer than 2 review ids; do not create a theme with no evidence.
- "keywords": lowercase terms that appear in the reviews of that theme.
- "interpretation": one sentence describing what this theme MEANS for the product team.
- "summary": one sentence describing what customers actually said (facts, not speculation).

Return JSON:
{{"themes": [{{"name": "...", "summary": "...", "interpretation": "...", "keywords": ["..."], "review_ids": ["..."]}}]}}"""


def _chunk_reviews(reviews: list[Review]) -> list[list[Review]]:
    return [reviews[i : i + CHUNK_SIZE] for i in range(0, len(reviews), CHUNK_SIZE)]


def _analyze_with_llm(
    reviews: list[Review], client: LLMClient, window_end: date, window_start: date
) -> AnalysisResult:
    reviews_by_id = {r.review_id: r for r in reviews}
    candidate_payload: list[dict] = []
    notes: list[str] = []
    assigned: dict[str, list[str]] = defaultdict(list)
    seen_candidates: set[str] = set()

    chunks = _chunk_reviews(reviews)
    for index, chunk in enumerate(chunks, start=1):
        lines = "\n".join(review_digest_line(r) for r in chunk)
        try:
            payload, _ = client.complete_json(
                CHUNK_SYSTEM, CHUNK_USER.format(n=len(chunk), lines=lines)
            )
        except LLMError as exc:
            notes.append(f"Batch {index}: {exc}")
            continue
        chunk_ids = {r.review_id for r in chunk}
        for theme in payload.get("themes", []) or []:
            if not isinstance(theme, dict):
                continue
            name = str(theme.get("name", "")).strip()
            if not name:
                continue
            ids = [str(i) for i in (theme.get("review_ids") or []) if str(i) in chunk_ids]
            if len(ids) < 1:
                continue
            key = name.lower()
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            candidate_payload.append(
                {
                    "name": name,
                    "summary": str(theme.get("summary", "")).strip(),
                    "keywords": [str(k) for k in (theme.get("keywords") or [])][:8],
                    "review_ids": ids,
                }
            )
            for rid in ids:
                assigned[rid].append(name)

    if not candidate_payload:
        notes.append("The LLM returned no usable candidate themes; falling back to the keyword taxonomy.")
        return _analyze_heuristic(reviews, window_end, window_start, notes)

    candidate_text = "\n".join(
        f"- name={c['name']} | keywords={', '.join(c['keywords'])} | "
        f"review_ids={', '.join(c['review_ids'])} | summary={c['summary']}"
        for c in candidate_payload
    )
    try:
        final, _ = client.complete_json(
            CONSOLIDATE_SYSTEM,
            CONSOLIDATE_USER.format(
                n=len(chunks),
                m=len(reviews),
                candidates=candidate_text,
                max_themes=get_settings().max_themes,
            ),
        )
    except LLMError as exc:
        notes.append(f"Consolidation failed ({exc}); using the batch-level candidates directly.")
        final = {"themes": candidate_payload}

    allowed_ids = set(reviews_by_id)
    themes: list[Theme] = []
    dropped = 0
    for index, raw in enumerate(final.get("themes", []) or [], start=1):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        if not name:
            continue
        ids = [str(i) for i in (raw.get("review_ids") or []) if str(i) in allowed_ids]
        if not ids:
            dropped += 1
            continue
        evidence = [build_evidence(reviews_by_id[rid]) for rid in ids]
        themes.append(
            Theme(
                theme_id=f"theme-{index}",
                name=name[:120],
                summary=str(raw.get("summary", "")).strip() or "No summary supplied by the model.",
                interpretation=str(raw.get("interpretation", "")).strip(),
                keywords=[str(k) for k in (raw.get("keywords") or [])][:10],
                review_ids=ids,
                evidence=evidence,
            )
        )

    if dropped:
        notes.append(f"{dropped} proposed theme(s) dropped: every cited review id was checked against the dataset.")

    if len(themes) > get_settings().max_themes:
        notes.append(
            f"Trimmed from {len(themes)} to {get_settings().max_themes} themes (hard limit)."
        )
        themes = themes[: get_settings().max_themes]

    covered = {rid for t in themes for rid in t.review_ids}
    unclassified = [r.review_id for r in reviews if r.review_id not in covered]

    rankings = rank_themes(themes, reviews_by_id, window_end)
    for theme in themes:
        theme.summary = theme.summary.strip()

    quotations = select_quotations(reviews, themes, limit=get_settings().max_quotations)

    return AnalysisResult(
        mode=AnalysisMode.LLM,
        themes=themes,
        rankings=rankings,
        quotations=quotations,
        unclassified_review_ids=unclassified,
        model_notes=notes,
        ranking_criteria={
            "weights": RANKING_WEIGHTS,
            "description": (
                "Themes are ranked on a 0-100 score: frequency 35%, recency 20%, "
                "severity 25%, mean rating 10%, evidence quality 10%. Counts, ratings "
                "and dates are measured from the dataset; names and summaries are model "
                "interpretation."
            ),
        },
    )


# --------------------------------------------------------------------------
# Heuristic pipeline
# --------------------------------------------------------------------------


def _analyze_heuristic(
    reviews: list[Review],
    window_end: date,
    window_start: date,
    notes: Optional[list[str]] = None,
) -> AnalysisResult:
    notes = list(notes or [])
    reviews_by_id = {r.review_id: r for r in reviews}
    buckets: dict[str, list[str]] = defaultdict(list)

    for review in reviews:
        haystack = f"{review.title or ''} {review.review_text}".lower()
        best_key = None
        best_hits = 0
        for key, _label, keywords in HEURISTIC_THEMES:
            hits = sum(1 for kw in keywords if kw in haystack)
            if hits > best_hits:
                best_key, best_hits = key, hits
        if best_key:
            buckets[best_key].append(review.review_id)

    themes: list[Theme] = []
    for index, (key, label, keywords) in enumerate(HEURISTIC_THEMES, start=1):
        ids = buckets.get(key, [])
        if not ids:
            continue
        themes.append(
            Theme(
                theme_id=f"theme-{index}",
                name=label,
                summary=(
                    f"Detected by the documented keyword taxonomy ({len(keywords)} terms) "
                    f"in {len(ids)} reviews."
                ),
                interpretation=(
                    "Keyword-derived grouping, not a model interpretation. Review wording "
                    "may differ from the label."
                ),
                keywords=list(keywords),
                review_ids=ids,
                evidence=[build_evidence(reviews_by_id[rid]) for rid in ids],
            )
        )

    rankings = rank_themes(themes, reviews_by_id, window_end)
    max_themes = get_settings().max_themes
    if len(themes) > max_themes:
        dropped = [t.name for t in themes[max_themes:]]
        notes.append(
            f"Trimmed from {len(themes)} to {max_themes} themes (hard limit). "
            f"Dropped (lowest ranked): {', '.join(dropped)}."
        )
        themes = themes[:max_themes]
        rankings = rank_themes(themes, reviews_by_id, window_end)
    covered = {rid for t in themes for rid in t.review_ids}
    unclassified = [r.review_id for r in reviews if r.review_id not in covered]
    quotations = select_quotations(reviews, themes, limit=get_settings().max_quotations)

    return AnalysisResult(
        mode=AnalysisMode.HEURISTIC,
        themes=themes,
        rankings=rankings,
        quotations=quotations,
        unclassified_review_ids=unclassified,
        model_notes=notes
        + [
            "No LLM credentials configured: themes were derived from the documented "
            "keyword taxonomy instead of a language model."
        ],
        ranking_criteria={
            "weights": RANKING_WEIGHTS,
            "description": (
                "Same 0-100 score as the LLM mode: frequency 35%, recency 20%, severity 25%, "
                "mean rating 10%, evidence quality 10%."
            ),
        },
    )


def analyze_themes(
    reviews: list[Review],
    client: Optional[LLMClient] = None,
    *,
    window_start: Optional[date] = None,
    window_end: Optional[date] = None,
) -> AnalysisResult:
    """Analyse the reviews into at most five ranked themes."""
    settings = get_settings()
    window_end = window_end or date.today()
    window_start = window_start or (window_end - timedelta(weeks=settings.default_weeks))
    if not reviews:
        return AnalysisResult(mode=AnalysisMode.HEURISTIC, ranking_criteria={"weights": RANKING_WEIGHTS})

    client = client or LLMClient()
    if client.available:
        try:
            return _analyze_with_llm(reviews, client, window_end, window_start)
        except LLMError as exc:
            result = _analyze_heuristic(reviews, window_end, window_start, [f"LLM analysis failed: {exc}"])
            return result
    return _analyze_heuristic(reviews, window_end, window_start)
