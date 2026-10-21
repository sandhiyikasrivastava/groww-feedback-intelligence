"""Collect real public customer reviews for the Groww app (Apple App Store, India).

Source of truth
---------------
Apple publishes a public customer-reviews RSS feed for every App Store app.
No credentials, no private APIs and no login are required, and the feed is
plain JSON served over HTTPS:

    https://itunes.apple.com/in/rss/customerreviews/page={1..10}/id={app_id}/sortby={feed}/json

``sortby=mostrecent`` gives the newest reviews first, ``sortby=mosthelpful``
gives a different (helpfulness-weighted) slice of the same corpus.  Both feeds
are fetched and merged so that the recency window can be covered as fully as
the public feed allows.

Google Play Store reviews could not be collected in this environment: the
Play Store web client only serves review data through an authenticated
``/_/PlayStoreUi/data/batchexecute`` RPC that answers ``PlayDataError`` to
anonymous requests.  See README -> "Data sources and collection limitations".

Usage
-----
    python scripts/collect_appstore_reviews.py --out data/collected/groww_reviews.csv
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles often default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from groww_intel.config import GROWW_APPSTORE_APP_ID, GROWW_APPSTORE_APP_NAME  # noqa: E402
from groww_intel.ingestion import infer_platform, infer_product_area  # noqa: E402

FEEDS = ("mostrecent", "mosthelpful")
MAX_PAGES = 10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
SSL_CTX = ssl.create_default_context()


def feed_url(page: int, feed: str, app_id: str = GROWW_APPSTORE_APP_ID) -> str:
    return (
        f"https://itunes.apple.com/in/rss/customerreviews/page={page}"
        f"/id={app_id}/sortby={feed}/json"
    )


def fetch_json(url: str, retries: int = 4, timeout: int = 30) -> dict[str, Any]:
    """Fetch JSON with polite exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"}
            )
            with urllib.request.urlopen(request, timeout=timeout, context=SSL_CTX) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
            last_error = exc
            if exc.code in (403, 429, 500, 502, 503):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except Exception as exc:  # pragma: no cover - network dependent
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def parse_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entries = payload.get("feed", {}).get("entry", []) or []
    parsed: list[dict[str, Any]] = []
    for entry in entries:
        if "im:rating" not in entry or "content" not in entry:
            continue  # first entry of a feed is a "feed metadata" pseudo-entry
        link = ""
        raw_link = entry.get("link", [])
        if isinstance(raw_link, str):
            link = raw_link
        else:
            for link_obj in raw_link or []:
                if isinstance(link_obj, dict) and link_obj.get("attributes", {}).get("rel") == "related":
                    link = link_obj.get("attributes", {}).get("href", "")
        parsed.append(
            {
                "review_id": entry.get("id", {}).get("label", ""),
                "date": entry.get("updated", {}).get("label", "")[:10],
                "rating": entry.get("im:rating", {}).get("label", ""),
                "title": entry.get("title", {}).get("label", ""),
                "review_text": entry.get("content", {}).get("label", ""),
                "author": entry.get("author", {}).get("name", {}).get("label", ""),
                "author_uri": entry.get("author", {}).get("uri", {}).get("label", ""),
                "app_version": entry.get("im:version", {}).get("label", ""),
                "feed_link": link,
            }
        )
    return parsed


def collect(app_id: str = GROWW_APPSTORE_APP_ID) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect and merge reviews from both public feeds."""
    merged: dict[str, dict[str, Any]] = {}
    feed_stats: list[dict[str, Any]] = []

    for feed in FEEDS:
        collected_for_feed = 0
        for page in range(1, MAX_PAGES + 1):
            url = feed_url(page, feed, app_id)
            try:
                payload = fetch_json(url)
            except RuntimeError as exc:
                feed_stats.append({"feed": feed, "page": page, "status": "failed", "detail": str(exc)})
                break
            entries = parse_entries(payload)
            if not entries:
                feed_stats.append({"feed": feed, "page": page, "status": "empty"})
                continue
            for entry in entries:
                if not entry["review_id"]:
                    continue
                entry["feed"] = feed
                if entry["review_id"] not in merged:
                    merged[entry["review_id"]] = entry
                    collected_for_feed += 1
            feed_stats.append({"feed": feed, "page": page, "status": "ok", "entries": len(entries)})
            time.sleep(1.0)  # polite rate limiting

        feed_stats.append({"feed": feed, "status": "feed_total", "unique_added": collected_for_feed})

    rows = list(merged.values())
    rows.sort(key=lambda r: (r["date"], r["review_id"]))
    return rows, feed_stats


def to_csv_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        text = " ".join((row.get("review_text") or "").split())
        area = infer_product_area(text)
        out.append(
            {
                "review_id": row["review_id"],
                "date": row["date"],
                "title": row.get("title") or "",
                "review_text": text,
                "rating": row.get("rating") or "",
                "product_area": area.value,
                "product_area_source": "derived_heuristic",
                "platform": infer_platform(text).value,
                "source": "Apple App Store (India storefront)",
                "source_url": row.get("feed_link")
                or f"https://apps.apple.com/in/app/id{GROWW_APPSTORE_APP_ID}",
                "author": row.get("author") or "",
                "app_version": row.get("app_version") or "",
                "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "collection_feed": row.get("feed", ""),
                "author_reference": row.get("author_uri") or "",
            }
        )
    return out


def write_outputs(rows: list[dict[str, Any]], out_path: Path, stats: list[dict[str, Any]]) -> None:
    import csv

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "review_id", "date", "title", "review_text", "rating", "product_area",
        "product_area_source", "platform", "source", "source_url", "author",
        "app_version", "collected_at", "collection_feed", "author_reference",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    manifest = {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Apple App Store customer reviews RSS feed (public, no auth)",
        "source_feed_urls": [
            feed_url(page, feed)
            for feed in FEEDS
            for page in range(1, MAX_PAGES + 1)
        ],
        "app": {"name": GROWW_APPSTORE_APP_NAME, "app_store_id": GROWW_APPSTORE_APP_ID,
                "storefront": "India (itunes.apple.com/in)"},
        "unique_reviews": len(rows),
        "date_range": {
            "earliest": min((r["date"] for r in rows), default=None),
            "latest": max((r["date"] for r in rows), default=None),
        },
        "feed_stats": stats,
        "notes": [
            "Reviews are verbatim public App Store submissions; no text was modified.",
            "product_area is derived by the documented heuristic in groww_intel/ingestion.py.",
            "source_url points to the app's public review page on the India storefront; "
            "the review_id in the feed is unique and preserved for traceability.",
        ],
    }
    manifest_path = out_path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out_path} ({len(rows)} reviews)")
    print(f"wrote {manifest_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect public Groww App Store reviews")
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "data" / "collected" / "groww_appstore_reviews.csv"),
    )
    args = parser.parse_args()

    rows, stats = collect()
    if not rows:
        print("No reviews collected — aborting without writing a file.", file=sys.stderr)
        return 1
    write_outputs(to_csv_rows(rows), Path(args.out), stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
