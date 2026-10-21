"""End-to-end orchestration of the analysis pipeline.

    CSV → ingest/validate → themes → fee issue → source verification →
    weekly pulse + customer explanation

The same function is used by the Streamlit app, the CLI script and the
tests, so the behaviour cannot drift between them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .analysis import analyze_themes
from .config import get_settings
from .fee_issue import investigate_fee_issue
from .ingestion import read_reviews_csv
from .llm import LLMClient
from .reports import generate_customer_explanation, generate_weekly_pulse
from .schemas import (
    AnalysisResult,
    CustomerExplanation,
    FeeIssueResult,
    Review,
    SourceVerificationResult,
    ValidationReport,
    WeeklyPulse,
)
from .sources import verify_sources


@dataclass
class PipelineResult:
    """Everything the UI and the reports need."""

    reviews: list[Review] = field(default_factory=list)
    validation: ValidationReport = field(default_factory=lambda: ValidationReport(file_name=""))
    analysis: AnalysisResult = field(default_factory=AnalysisResult)
    fee_result: FeeIssueResult = field(default_factory=FeeIssueResult)
    verification: SourceVerificationResult = field(default_factory=SourceVerificationResult)
    pulse: Optional[WeeklyPulse] = None
    explanation: Optional[CustomerExplanation] = None
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    errors: list[str] = field(default_factory=list)

    @property
    def review_date_range(self) -> str:
        if not self.reviews:
            return "n/a"
        return f"{self.reviews[0].date.isoformat()} → {self.reviews[-1].date.isoformat()}"


def run_pipeline(
    csv_path: str | Path,
    *,
    weeks: Optional[int] = None,
    as_of: Optional[date] = None,
    client: Optional[LLMClient] = None,
    skip_sources: bool = False,
) -> PipelineResult:
    """Run the whole analysis pipeline over one CSV file."""
    settings = get_settings()
    weeks = weeks or settings.default_weeks
    as_of = as_of or date.today()
    window_start = as_of - timedelta(weeks=weeks)
    client = client or LLMClient()

    result = PipelineResult(window_start=window_start, window_end=as_of)

    ingestion = read_reviews_csv(csv_path, as_of=as_of, weeks=weeks)
    result.reviews = ingestion.reviews
    result.validation = ingestion.report

    result.analysis = analyze_themes(
        result.reviews,
        client,
        window_start=window_start,
        window_end=as_of,
    )
    result.fee_result = investigate_fee_issue(result.reviews, window_end=as_of)

    if skip_sources:
        result.verification = SourceVerificationResult()
    else:
        try:
            result.verification = verify_sources(as_of=as_of)
        except Exception as exc:  # pragma: no cover - defensive
            result.errors.append(f"Source verification failed: {exc}")

    result.pulse = generate_weekly_pulse(
        result.reviews,
        result.analysis,
        result.fee_result,
        window_start=window_start,
        window_end=as_of,
        client=client,
    )
    result.explanation = generate_customer_explanation(
        result.fee_result,
        result.verification,
        client=client,
    )
    return result
