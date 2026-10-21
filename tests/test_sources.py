"""Official source verification."""

from __future__ import annotations

import json
from datetime import date

import pytest

from groww_intel import sources as sources_module
from groww_intel.sources import (
    FactSpec,
    OfficialSource,
    SourceSpec,
    fetch_source_text,
    grounding_violations,
    html_to_text,
    load_snapshot,
    save_snapshot,
    verify_fact,
    verify_sources,
)

PRICING_HTML = """
<html><body>
<script>window.__DATA = {"pricing": "Equity Brokerage \\u20b920 0\\.1% per executed order whichever is lower, minimum \\u20b95. DP charges (per sell transaction) Depository: \\u20b93.5 (male) Groww: \\u20b916.5. 18% GST On Brokerage, DP charges."}</script>
</body></html>
"""


def test_html_to_text_recovers_embedded_pricing_strings():
    text = html_to_text(PRICING_HTML)
    assert "0.1%" in text
    assert "₹20" in text
    assert "minimum ₹5" in text


def test_fact_is_verified_when_the_wording_is_found():
    text = html_to_text(PRICING_HTML)
    spec = FactSpec(
        fact_id="equity_brokerage",
        label="Equity brokerage",
        value="₹20 or 0.1%, minimum ₹5",
        source_url="https://groww.in/pricing/",
        required_tokens=("0.1%", "₹5"),
        any_of_tokens=("₹20",),
    )
    fact = verify_fact(spec, text)
    assert fact.verified
    assert fact.confidence == "verified"
    assert fact.quote


def test_fact_is_rejected_when_the_wording_is_absent():
    spec = FactSpec(
        fact_id="missing",
        label="Not on the page",
        value="99% levy",
        source_url="https://groww.in/pricing/",
        required_tokens=("99%",),
        any_of_tokens=("levy",),
    )
    fact = verify_fact(spec, html_to_text(PRICING_HTML))
    assert not fact.verified
    assert fact.confidence in {"uncertain", "not_found"}
    assert fact.note


def test_grounding_violations_flags_numbers_that_are_not_verified():
    allowed = "brokerage is 0.1% with a minimum of ₹5 and 18% GST"
    assert grounding_violations("You pay ₹20 per order", allowed) == ["20"]
    assert grounding_violations("You pay 0.1% and 18% GST", allowed) == []


def test_verify_sources_reports_failures_and_never_invents(monkeypatch):
    def fake_fetch(url, timeout=30.0):
        raise sources_module.SourceFetchError(f"{url} returned HTTP 403")

    monkeypatch.setattr(sources_module, "fetch_source_text", fake_fetch)
    result = verify_sources(
        sources=[SourceSpec("https://groww.in/pricing/", "pricing", "fees")],
        facts=[
            FactSpec(
                fact_id="x",
                label="x",
                value="v",
                source_url="https://groww.in/pricing/",
                required_tokens=("₹20",),
            )
        ],
        allow_snapshot_fallback=False,
    )
    assert result.facts[0].verified is False
    assert result.sources[0].fetch_status == "failed"
    assert result.unverified_claims == ["x"]


def test_snapshot_fallback_is_used_and_labelled(monkeypatch, tmp_path):
    monkeypatch.setattr(sources_module, "SNAPSHOT_DIR", tmp_path)
    url = "https://groww.in/pricing/"
    save_snapshot(url, html_to_text(PRICING_HTML), captured_at=date(2026, 10, 1))

    def fake_fetch(u, timeout=30.0):
        raise sources_module.SourceFetchError("HTTP 403")

    monkeypatch.setattr(sources_module, "fetch_source_text", fake_fetch)
    result = verify_sources(
        sources=[SourceSpec(url, "pricing", "fees")],
        facts=[
            FactSpec(
                fact_id="equity_brokerage",
                label="Equity brokerage",
                value="₹20 or 0.1%, minimum ₹5",
                source_url=url,
                required_tokens=("0.1%", "₹5"),
                any_of_tokens=("₹20",),
            )
        ],
    )
    source = result.sources[0]
    assert source.fetch_status == "snapshot"
    assert source.checked_at == date(2026, 10, 1)
    assert "snapshot" in source.note.lower()
    assert result.facts[0].verified
    assert result.last_checked == date(2026, 10, 1)


def test_snapshot_metadata_is_json(tmp_path, monkeypatch):
    monkeypatch.setattr(sources_module, "SNAPSHOT_DIR", tmp_path)
    save_snapshot("https://groww.in/pricing/", "text", captured_at=date(2026, 10, 1))
    text, meta = load_snapshot("https://groww.in/pricing/")
    assert text == "text"
    assert meta["captured_at"] == "2026-10-01"
    assert len(meta["sha256"]) == 64


def test_fetch_failure_raises(monkeypatch):
    def fake_get(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(sources_module.httpx, "Client", fake_get)
    with pytest.raises(sources_module.SourceFetchError):
        fetch_source_text("https://groww.in/pricing/")


@pytest.mark.parametrize(
    "url",
    [
        "https://groww.in/pricing/",
        "https://groww.in/help/stocks/sx-pricing/what-is-the-charge-for-buy-order-delivery",
        "https://groww.in/help/stocks/sx-pricing/what-are-intraday-charges",
        "https://groww.in/help/stocks/sx-pricing/how-much-gst-is-charged",
        "https://groww.in/calculators/brokerage-calculator/",
    ],
)
def test_every_official_source_is_a_groww_url(url):
    assert url.startswith("https://groww.in/")
