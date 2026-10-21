"""Verification of fee facts against official Groww sources.

The module never invents a fee.  A fact is only reported as *verified* when
its required tokens are found in the text of an official Groww page that was
actually fetched (or, if the live fetch was blocked, in a dated snapshot of
that page).  Anything that cannot be verified is flagged and excluded from
the customer-facing explanation.

Official sources used (all first-party groww.in URLs):
    https://groww.in/pricing/
    https://groww.in/help/stocks/sx-pricing/what-is-the-charge-for-buy-order-delivery
    https://groww.in/help/stocks/sx-pricing/what-are-intraday-charges
    https://groww.in/help/stocks/sx-pricing/how-much-gst-is-charged
    https://groww.in/calculators/brokerage-calculator/
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import httpx

from .config import SNAPSHOT_DIR, get_settings
from .schemas import OfficialSource, SourceFact, SourceVerificationResult

#: Browser-like headers.  groww.in rejects requests that do not look like a
#: normal browser navigation, so a realistic header set is required.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="121", "Not A(Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}

PRICING_URL = "https://groww.in/pricing/"
DELIVERY_HELP_URL = "https://groww.in/help/stocks/sx-pricing/what-is-the-charge-for-buy-order-delivery"
INTRADAY_HELP_URL = "https://groww.in/help/stocks/sx-pricing/what-are-intraday-charges"
GST_HELP_URL = "https://groww.in/help/stocks/sx-pricing/how-much-gst-is-charged"
CALCULATOR_URL = "https://groww.in/calculators/brokerage-calculator/"


@dataclass(frozen=True)
class SourceSpec:
    url: str
    title: str
    purpose: str


@dataclass(frozen=True)
class FactSpec:
    fact_id: str
    label: str
    value: str
    source_url: str
    required_tokens: tuple[str, ...]
    any_of_tokens: tuple[str, ...] = ()
    customer_phrasing: str = ""
    note: str = ""


OFFICIAL_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(PRICING_URL, "Groww — Brokerage Charges & Pricing",
               "Master fee table: brokerage, DP charges, statutory charges, GST."),
    SourceSpec(DELIVERY_HELP_URL, "Groww Help — What is the charge for buy order (delivery)?",
               "Itemised delivery trading charges with worked figures."),
    SourceSpec(INTRADAY_HELP_URL, "Groww Help — What are intraday charges?",
               "Intraday brokerage, statutory charges and auto square-off."),
    SourceSpec(GST_HELP_URL, "Groww Help — How much GST is charged?",
               "The 18% GST rate and the charges it applies to."),
    SourceSpec(CALCULATOR_URL, "Groww — Brokerage Calculator",
               "Official tool customers can use to see charges before trading."),
)

FEE_FACTS: tuple[FactSpec, ...] = (
    FactSpec(
        fact_id="equity_brokerage",
        label="Equity brokerage (delivery & intraday)",
        value="₹20 or 0.1% of the order value per executed order, whichever is lower, with a minimum of ₹5.",
        source_url=PRICING_URL,
        required_tokens=("0.1%", "₹5"),
        any_of_tokens=("₹20",),
        customer_phrasing=(
            "Brokerage on an equity order is ₹20 or 0.1% of the order value, whichever is "
            "lower, and is never less than ₹5."
        ),
    ),
    FactSpec(
        fact_id="dp_charges",
        label="DP charges (per sell transaction)",
        value="On each sell transaction: depository charge ₹3.50 (male) / ₹3.25 (female) plus Groww's ₹16.50; Groww's fee is waived when the debit value is below ₹100.",
        source_url=PRICING_URL,
        required_tokens=("₹16.5",),
        any_of_tokens=("₹3.5", "₹3.25", "16.50"),
        customer_phrasing=(
            "Each sale from your demat account also carries a DP charge: ₹3.50 (depository, "
            "male) or ₹3.25 (female) plus Groww's ₹16.50 — and Groww waives its ₹16.50 when "
            "the sale value is below ₹100."
        ),
    ),
    FactSpec(
        fact_id="gst",
        label="GST on charges",
        value="18% GST applies on brokerage, DP charges, exchange transaction charges, IPFT, SEBI turnover charges and other listed charges.",
        source_url=GST_HELP_URL,
        required_tokens=("18%", "GST"),
        customer_phrasing=(
            "18% GST is added on top of brokerage, DP and the other charges listed by Groww."
        ),
    ),
    FactSpec(
        fact_id="stamp_duty",
        label="Stamp duty (delivery buys)",
        value="0.015% of the buying value for delivery purchases (₹0 on sales).",
        source_url=PRICING_URL,
        required_tokens=("0.015%",),
        any_of_tokens=("Stamp Duty", "stamp duty"),
        customer_phrasing=(
            "Stamp duty is 0.015% of the buying value when you buy for delivery (₹0 when you sell)."
        ),
    ),
    FactSpec(
        fact_id="stt",
        label="STT (securities transaction tax)",
        value="0.1% of the transaction value on delivery buys and sells; 0.025% on intraday sells.",
        source_url=PRICING_URL,
        required_tokens=("0.1%",),
        any_of_tokens=("STT", "Securities Transaction Tax"),
        customer_phrasing=(
            "STT is a government tax of 0.1% of the transaction value on delivery buys and sells "
            "(0.025% on intraday sells)."
        ),
    ),
    FactSpec(
        fact_id="exchange_sebi",
        label="Exchange transaction, SEBI turnover and IPFT charges",
        value="Exchange transaction charge 0.00297% (NSE) / 0.00375% (BSE); SEBI turnover charge 0.0001%; IPFT charge 0.0001% (NSE).",
        source_url=PRICING_URL,
        required_tokens=("0.00297%", "0.0001%"),
        any_of_tokens=("SEBI Turnover",),
        customer_phrasing=(
            "Small levies also apply: 0.00297% (NSE) or 0.00375% (BSE) exchange transaction "
            "charge, 0.0001% SEBI turnover charge and 0.0001% investor protection fund charge."
        ),
    ),
    FactSpec(
        fact_id="account_maintenance",
        label="Account maintenance & opening",
        value="₹0 account opening (including trading) and ₹0 account maintenance charges.",
        source_url=PRICING_URL,
        required_tokens=("Account Maintenance Charges",),
        any_of_tokens=("₹0",),
        customer_phrasing=(
            "There is no account opening charge and no account maintenance (AMC) charge on Groww."
        ),
    ),
    FactSpec(
        fact_id="intraday_minimum",
        label="Minimum brokerage rule for intraday",
        value="If brokerage for an intraday trade works out below ₹5, the minimum charged is the lower of ₹5 or 2.5% of the trade value.",
        source_url=INTRADAY_HELP_URL,
        required_tokens=("2.5%", "₹5"),
        any_of_tokens=("intraday",),
        customer_phrasing=(
            "On intraday trades, if the brokerage works out to less than ₹5, the minimum "
            "charged is the lower of ₹5 or 2.5% of the trade value."
        ),
    ),
    FactSpec(
        fact_id="auto_square_off",
        label="Auto square-off charge",
        value="₹50 per open intraday position that the system squares off, plus GST.",
        source_url=INTRADAY_HELP_URL,
        required_tokens=("₹50",),
        any_of_tokens=("square off", "square-off", "Square-off"),
        customer_phrasing=(
            "If an intraday position is not closed by 3:20 PM, Groww squares it off and charges "
            "₹50 per position plus GST."
        ),
    ),
    FactSpec(
        fact_id="delivery_sell_charges",
        label="Delivery sell-side charges",
        value=(
            "On a delivery sale, DP charge is ₹3.50 (male) / ₹3.25 (female) depository charge "
            "plus ₹16.50 Groww charge (₹0 Groww fee when the debit value is below ₹100), plus GST; "
            "stamp duty is 0.015% of the buying value on buys and ₹0 on sells; STT is 0.1% of "
            "the transaction value on delivery buys and sells."
        ),
        source_url=DELIVERY_HELP_URL,
        required_tokens=("16.50", "0.015%"),
        any_of_tokens=("DP charge", "DP Charges"),
        customer_phrasing=(
            "On a delivery sale the DP charge is ₹3.50 (male) or ₹3.25 (female) plus Groww's "
            "₹16.50 (waived when the sale value is below ₹100), plus GST; stamp duty is 0.015% "
            "of the buying value and STT is 0.1% of the transaction value on delivery buys and sells."
        ),
    ),
    FactSpec(
        fact_id="brokerage_calculator",
        label="Official brokerage calculator",
        value="Groww publishes a brokerage calculator so customers can estimate the charges on a trade before placing it.",
        source_url=CALCULATOR_URL,
        required_tokens=("Brokerage Calculator",),
        customer_phrasing=(
            "You can estimate the charges on a trade before placing it with Groww's official "
            "brokerage calculator."
        ),
    ),
)


# --------------------------------------------------------------------------
# Fetching + text extraction
# --------------------------------------------------------------------------


class SourceFetchError(Exception):
    """Raised when an official source cannot be retrieved."""


def _script_prose(raw_html: str) -> str:
    """Recover prose that lives inside the page's embedded JSON payload.

    Some Groww help-centre articles render their body client-side: the text
    is present in the page only as a string inside the Next.js data payload.
    Stripping ``<script>`` blocks would lose the article entirely, so quoted
    strings that look like prose are decoded and appended.
    """
    prose: list[str] = []
    for match in re.finditer(r"<script[^>]*>(.*?)</script>", raw_html, flags=re.S):
        chunk = match.group(1)
        chunk = (
            chunk.replace("\\u20b9", "₹")
            .replace("\\u003c", "<")
            .replace("\\u003e", ">")
            .replace("\\u0026", "&")
            .replace("\\u2013", "-")
            .replace("\\u2014", "-")
            .replace("\\.", ".")
        )
        for literal in re.findall(r'"(?:[^"\\]|\\.){40,6000}"', chunk):
            try:
                value = json.loads(literal)
            except (json.JSONDecodeError, ValueError):
                value = literal.strip('"')
            if not isinstance(value, str):
                continue
            if len(value.split()) >= 6 and len(value) <= 4000:
                prose.append(value)
    return " ".join(prose)


def html_to_text(raw_html: str) -> str:
    """Turn a page into normalised, searchable plain text."""
    text = raw_html
    text = text.replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&")
    text = text.replace('\\"', '"').replace("\\u20b9", "₹").replace("\\.", ".")
    text = text.replace("\\-", "-").replace("\\u2013", "-").replace("\\u2014", "-")
    text = text.replace("\\n", " ").replace("\\/", "/")
    embedded = _script_prose(raw_html)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[\u200b\u00a0]", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    if embedded:
        text = f"{text} {embedded}".strip()
    return text


def _snapshot_paths(url: str) -> tuple[Path, Path]:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return SNAPSHOT_DIR / f"{digest}.txt", SNAPSHOT_DIR / f"{digest}.json"


def save_snapshot(url: str, text: str, captured_at: Optional[date] = None) -> Path:
    """Persist a dated snapshot of a source page (fallback when live fetch fails)."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    text_path, meta_path = _snapshot_paths(url)
    text_path.write_text(text, encoding="utf-8")
    meta = {
        "url": url,
        "captured_at": (captured_at or date.today()).isoformat(),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return text_path


def load_snapshot(url: str) -> Optional[tuple[str, dict]]:
    text_path, meta_path = _snapshot_paths(url)
    if not text_path.exists() or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return text_path.read_text(encoding="utf-8"), meta


def fetch_source_text(url: str, timeout: float = 30.0) -> str:
    """Fetch an official page and return its readable text.

    Raises :class:`SourceFetchError` when the page cannot be retrieved.
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url, headers=BROWSER_HEADERS)
    except Exception as exc:  # network failures
        raise SourceFetchError(f"Network error while fetching {url}: {exc}") from exc
    if response.status_code >= 400:
        raise SourceFetchError(f"{url} returned HTTP {response.status_code}")
    text = html_to_text(response.text)
    if not text:
        raise SourceFetchError(f"{url} returned an empty document")
    if "Uh Oh! 500" in text or "Some Error Occured" in text:
        raise SourceFetchError(f"{url} returned Groww's error page (article slug not found)")
    return text


def _compact(text: str) -> str:
    """Remove whitespace so '₹ 50' and '₹50' compare equal."""
    return re.sub(r"\s+", "", text).lower()


def _token_present(token: str, text: str) -> bool:
    return _compact(token) in _compact(text)


def _context_quote(text: str, token: str, width: int = 220) -> str:
    index = text.lower().find(token.lower())
    if index == -1:
        # whitespace-insensitive fallback
        compact = _compact(text)
        position = compact.find(_compact(token))
        if position == -1:
            return ""
        start = max(0, position - width // 3)
        end = min(len(compact), position + len(_compact(token)) + width)
        return compact[start:end]
    start = max(0, index - width // 3)
    end = min(len(text), index + len(token) + width)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = snippet.split(" ", 1)[-1]
    if end < len(text):
        snippet = snippet.rsplit(" ", 1)[0]
    return snippet


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def verify_fact(spec: FactSpec, text: str) -> SourceFact:
    """Check one fact against the retrieved text of its official source."""
    required = [t for t in spec.required_tokens if _token_present(t, text)]
    missing = [t for t in spec.required_tokens if not _token_present(t, text)]
    any_hit = [t for t in spec.any_of_tokens if _token_present(t, text)]
    any_missing = [t for t in spec.any_of_tokens if not _token_present(t, text)]

    verified = not missing and (not spec.any_of_tokens or bool(any_hit))
    quote = ""
    for token in list(spec.required_tokens) + list(spec.any_of_tokens):
        found = _context_quote(text, token)
        if found:
            quote = found
            break

    if verified:
        confidence = "verified"
        note = ""
    elif missing and not any_missing:
        confidence = "uncertain"
        note = (
            "Some of the expected wording was not found in the source page "
            f"(missing: {', '.join(missing)}); the claim is not used in the customer explanation."
        )
    else:
        confidence = "not_found"
        note = (
            "The source page did not contain the expected wording "
            f"(missing: {', '.join(missing + any_missing)}); the claim is excluded."
        )

    return SourceFact(
        fact_id=spec.fact_id,
        label=spec.label,
        value=spec.value,
        source_url=spec.source_url,
        quote=quote,
        verified=verified,
        confidence=confidence,
        note=note,
    )


def verify_sources(
    sources: Optional[Iterable[SourceSpec]] = None,
    facts: Optional[Iterable[FactSpec]] = None,
    *,
    allow_snapshot_fallback: bool = True,
    as_of: Optional[date] = None,
) -> SourceVerificationResult:
    """Fetch every official source and verify every fee fact.

    A fact is only verified when the wording was found in the text of the
    source that was actually retrieved.  If a live fetch fails and a dated
    snapshot exists, the snapshot is used and the result says so.
    """
    sources = list(sources or OFFICIAL_SOURCES)
    facts = list(facts or FEE_FACTS)
    as_of = as_of or date.today()

    texts: dict[str, str] = {}
    source_records: list[OfficialSource] = []

    for spec in sources:
        record = OfficialSource(url=spec.url, title=spec.title, purpose=spec.purpose)
        try:
            text = fetch_source_text(spec.url)
        except SourceFetchError as exc:
            record.fetch_status = "failed"
            record.note = f"Live fetch failed: {exc}"
            snapshot = load_snapshot(spec.url) if allow_snapshot_fallback else None
            if snapshot:
                snapshot_text, meta = snapshot
                texts[spec.url] = snapshot_text
                record.fetch_status = "snapshot"
                record.checked_at = date.fromisoformat(meta["captured_at"])
                record.snapshot_path = str(_snapshot_paths(spec.url)[0])
                record.content_sha256 = meta.get("sha256")
                record.note = (
                    f"Live fetch failed ({exc}); verified against a snapshot captured on "
                    f"{meta['captured_at']}."
                )
            else:
                record.note = f"Live fetch failed and no snapshot is available: {exc}"
        else:
            texts[spec.url] = text
            record.fetch_status = "live"
            record.checked_at = as_of
            record.content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            record.note = "Fetched live from groww.in."
        source_records.append(record)

    fact_records: list[SourceFact] = []
    unverified: list[str] = []
    for spec in facts:
        text = texts.get(spec.source_url)
        if text is None:
            fact_records.append(
                SourceFact(
                    fact_id=spec.fact_id,
                    label=spec.label,
                    value=spec.value,
                    source_url=spec.source_url,
                    verified=False,
                    confidence="not_found",
                    note="The source page could not be retrieved, so this claim is unverified.",
                )
            )
            unverified.append(spec.label)
            continue
        fact = verify_fact(spec, text)
        fact_records.append(fact)
        if not fact.verified:
            unverified.append(spec.label)

    for record in source_records:
        record.facts_verified = [
            f.fact_id for f in fact_records if f.source_url == record.url and f.verified
        ]

    checked_dates = [r.checked_at for r in source_records if r.checked_at]
    last_checked = min(checked_dates) if checked_dates else None

    return SourceVerificationResult(
        sources=source_records,
        facts=fact_records,
        last_checked=last_checked,
        unverified_claims=unverified,
    )


def verified_source_text(result: SourceVerificationResult) -> str:
    """All source text that backed a verified fact (used for grounding checks)."""
    chunks: list[str] = []
    for fact in result.facts:
        if fact.verified:
            chunks.append(fact.value)
            chunks.append(fact.quote)
    return " ".join(chunks)


def grounding_violations(text: str, allowed_text: str) -> list[str]:
    """Numbers in ``text`` that do not appear in ``allowed_text``.

    This is the guard that stops the customer-facing explanation from
    containing a fee amount that was never verified.
    """
    numbers = set(re.findall(r"\d+(?:\.\d+)?%?", text))
    allowed = allowed_text.lower()
    return sorted(n for n in numbers if n.lower() not in allowed)
