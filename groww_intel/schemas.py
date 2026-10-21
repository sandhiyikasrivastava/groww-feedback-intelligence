"""Structured schemas shared across the pipeline.

Every value that an LLM or an external integration produces is validated
against one of these models *before* it can reach a report or a tool call.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductArea(str, Enum):
    """Canonical Groww product areas.

    The taxonomy deliberately keeps stocks-delivery, intraday, F&O, MTF,
    mutual funds, demat account, IPO, commodities, payments and credit
    separate so that a fee issue observed in one area is never silently
    generalised to another.
    """

    STOCKS_DELIVERY = "Stocks (delivery)"
    STOCKS_INTRADAY = "Stocks (intraday)"
    FUTURES_OPTIONS = "Futures & Options"
    MTF = "Margin / MTF"
    MUTUAL_FUNDS = "Mutual Funds"
    DEMAT_ACCOUNT = "Demat account"
    IPO = "IPO"
    COMMODITIES = "Commodities"
    PAYMENTS = "Payments & withdrawals"
    CREDIT = "Credit"
    PLATFORM = "Platform / app-wide"
    UNKNOWN = "Unclassified"


class Platform(str, Enum):
    IOS_APP_STORE = "iOS App Store"
    ANDROID_PLAY_STORE = "Android Play Store"
    WEB = "Web"
    OTHER = "Other"
    UNKNOWN = "Unknown"


class AnalysisMode(str, Enum):
    LLM = "llm"
    HEURISTIC = "heuristic"


class Review(BaseModel):
    """A single, validated customer review."""

    model_config = ConfigDict(frozen=True)

    review_id: str = Field(min_length=1)
    date: date
    review_text: str = Field(min_length=1)
    rating: Optional[float] = Field(default=None, ge=1, le=5)
    product_area: ProductArea = ProductArea.UNKNOWN
    product_area_source: Literal["source", "derived_heuristic", "unknown"] = "unknown"
    platform: Platform = Platform.UNKNOWN
    source: Optional[str] = None
    source_url: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    app_version: Optional[str] = None

    @field_validator("review_text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("review_text is empty after normalisation")
        return cleaned


class ExcludedRecord(BaseModel):
    """A record that did not make it into the analysed dataset."""

    row_number: Optional[int] = None
    review_id: Optional[str] = None
    reason_code: str
    reason: str
    raw: dict = Field(default_factory=dict)


class ValidationReport(BaseModel):
    """Outcome of the CSV ingestion + validation stage."""

    file_name: str
    rows_read: int = 0
    accepted: int = 0
    excluded: int = 0
    deduplicated: int = 0
    duplicates_removed: int = 0
    date_range_start: Optional[date] = None
    date_range_end: Optional[date] = None
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    column_map: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    excluded_records: list[ExcludedRecord] = Field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"read={self.rows_read} accepted={self.accepted} "
            f"excluded={self.excluded} deduplicated={self.duplicates_removed}"
        )


class ThemeEvidence(BaseModel):
    """One review that supports a theme."""

    review_id: str
    date: date
    rating: Optional[float] = None
    product_area: ProductArea = ProductArea.UNKNOWN
    excerpt: str
    source_url: Optional[str] = None


class Theme(BaseModel):
    """A theme discovered in the review data.

    ``review_ids`` are the model's claimed assignments; ``evidence`` is the
    validated subset that was actually found in the dataset.  Counts exposed
    to the UI are always recomputed from ``evidence`` (measured fact), never
    copied from the model (interpretation).
    """

    theme_id: str
    name: str = Field(min_length=3, max_length=120)
    summary: str = Field(min_length=3)
    interpretation: str = ""
    review_ids: list[str] = Field(default_factory=list)
    evidence: list[ThemeEvidence] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    @property
    def measured_count(self) -> int:
        return len(self.evidence)


class ThemeRanking(BaseModel):
    """Ranking of themes with the transparent criteria used."""

    theme_id: str
    name: str
    count: int
    frequency_score: float
    recency_score: float
    severity_score: float
    rating_score: float
    evidence_quality_score: float
    total_score: float
    rank: int
    components_explained: dict = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    """Themes + supporting quotations for the analysed dataset."""

    mode: AnalysisMode = AnalysisMode.HEURISTIC
    themes: list[Theme] = Field(default_factory=list)
    rankings: list[ThemeRanking] = Field(default_factory=list)
    quotations: list[ThemeEvidence] = Field(default_factory=list)
    unclassified_review_ids: list[str] = Field(default_factory=list)
    model_notes: list[str] = Field(default_factory=list)
    ranking_criteria: dict = Field(default_factory=dict)


class FeeCategory(str, Enum):
    BROKERAGE = "Brokerage / order execution charge"
    DP_CHARGES = "DP (depository participant) charges"
    STATUTORY = "Statutory / regulatory charges (STT, stamp duty, turnover)"
    GST = "GST on charges"
    PLATFORM = "Platform / subscription / account fees"
    DEMAT_OTHER = "Other demat & account charges"
    MTF = "MTF interest & related charges"
    MUTUAL_FUND = "Mutual fund charges (exit load, expense ratio)"
    OTHER = "Other charges"


class FeeIssueCandidate(BaseModel):
    """A candidate recurring fee issue with its supporting evidence."""

    issue_id: str
    label: str
    fee_category: FeeCategory
    product_area: ProductArea
    supporting_review_ids: list[str] = Field(default_factory=list)
    explicit_mentions: int = 0
    distinct_weeks: int = 0
    distinct_authors: int = 0
    mean_rating: Optional[float] = None
    low_rating_share: float = 0.0
    quotes: list[ThemeEvidence] = Field(default_factory=list)
    other_area_review_ids: list[str] = Field(default_factory=list)
    related_unclassified_review_ids: list[str] = Field(default_factory=list)
    area_stated: bool = True
    meets_threshold: bool = False
    threshold_detail: dict = Field(default_factory=dict)
    interpretation: str = ""


class FeeIssueResult(BaseModel):
    """Outcome of the fee-issue investigation."""

    mode: AnalysisMode = AnalysisMode.HEURISTIC
    selected: Optional[FeeIssueCandidate] = None
    candidates: list[FeeIssueCandidate] = Field(default_factory=list)
    insufficient_evidence: bool = False
    limitation_note: str = ""
    evidence_threshold: dict = Field(default_factory=dict)


class SourceFact(BaseModel):
    """A single fact that was verified against an official source."""

    fact_id: str
    label: str
    value: str
    source_url: str
    quote: str = ""
    verified: bool = False
    confidence: Literal["verified", "uncertain", "not_found"] = "not_found"
    note: str = ""


class OfficialSource(BaseModel):
    """An official Groww source used for verification."""

    url: str
    title: str
    purpose: str
    checked_at: Optional[date] = None
    fetch_status: Literal["live", "snapshot", "failed"] = "failed"
    snapshot_path: Optional[str] = None
    content_sha256: Optional[str] = None
    facts_verified: list[str] = Field(default_factory=list)
    note: str = ""


class SourceVerificationResult(BaseModel):
    """All source verification results for one report."""

    sources: list[OfficialSource] = Field(default_factory=list)
    facts: list[SourceFact] = Field(default_factory=list)
    last_checked: Optional[date] = None
    unverified_claims: list[str] = Field(default_factory=list)


class Recommendation(BaseModel):
    """One actionable recommendation."""

    title: str
    problem: str
    intervention: str
    measurable_outcome: str


class WeeklyPulse(BaseModel):
    """The internal weekly product pulse (<= 250 words)."""

    title: str = "Weekly Product Pulse"
    body_markdown: str
    word_count: int
    leading_themes: list[str] = Field(default_factory=list)
    quotations: list[ThemeEvidence] = Field(default_factory=list)
    key_issue: str = ""
    recommendations: list[Recommendation] = Field(default_factory=list)
    mode: AnalysisMode = AnalysisMode.HEURISTIC
    warnings: list[str] = Field(default_factory=list)


class CustomerExplanation(BaseModel):
    """The customer-facing fee explanation (<= 6 bullets)."""

    title: str = "How Groww charges work on your trades"
    bullets: list[str] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)
    last_checked: Optional[date] = None
    mode: AnalysisMode = AnalysisMode.HEURISTIC
    excluded_claims: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DocRecord(BaseModel):
    """The structured record appended to the documentation service."""

    date: str
    top_themes: list[str] = Field(default_factory=list)
    weekly_pulse: str
    identified_fee_issue: str
    explanation_bullets: list[str] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)
    review_date_range: str = ""
    reviews_analyzed: int = 0
    supporting_evidence: list[str] = Field(default_factory=list)
    execution_status: str = "pending"
    extra: dict = Field(default_factory=dict)


class ExecutionReceipt(BaseModel):
    """Result of an external write."""

    status: Literal["success", "simulated", "failed", "duplicate_prevented"]
    external_id: Optional[str] = None
    destination: str
    detail: str = ""
    simulated: bool = False
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_response: Optional[dict] = None
