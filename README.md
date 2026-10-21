# Groww Feedback Intelligence

**AI-powered customer feedback intelligence and support communication workflow for
[Groww](https://groww.in/) — the Indian investing platform.**

Real public customer reviews → validated dataset → ranked themes → a *discovered*
recurring fee confusion → a customer-facing fee explanation verified against
official Groww documentation → an internal weekly product pulse → **human-approved**
append to a documentation service and **human-approved** email draft.

Everything in the app is computed from the uploaded CSV and the live official
Groww pages. There are no hard-coded findings, fake reviews or pre-baked
outputs.

---

## 1. Live demo

| | |
|---|---|
| **Public URL (temporary tunnel to a local run)** | https://cart-enterprises-evans-conversion.trycloudflare.com |
| **Source repository** | https://github.com/sandhiyikasrivastava/groww-feedback-intelligence |
| **One-click deploy to Streamlit Community Cloud** | [Deploy](https://share.streamlit.io/deploy?url=https://github.com/sandhiyikasrivastava/groww-feedback-intelligence) |

> The `trycloudflare.com` link is a temporary tunnel to a Streamlit process
> running on a developer machine. It stays up only while that process runs and
> has no uptime guarantee. The durable deployment is the Streamlit Community
> Cloud link above: sign in with GitHub, paste the secrets (section 4) and the
> app is deployed from the repository. The application path is the repository
> root (`app.py`).

---

## 2. Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │                 app.py (Streamlit)          │
                    │  upload ▸ validate ▸ themes ▸ fee issue ▸    │
                    │  sources ▸ edit reports ▸ approve ▸ execute │
                    └───────────────┬──────────────────────────────┘
                                    │  groww_intel.pipeline.run_pipeline()
   ┌────────────────────────────────┼──────────────────────────────────────────────┐
   │                                │                                              │
┌──▼───────────────┐  ┌────────────▼─────────────┐  ┌──────────────┐  ┌──────────────┐
│  ingestion.py    │  │      analysis.py          │  │  fee_issue.py│  │  sources.py  │
│ ──────────────── │  │ ────────────────────────  │  │ ──────────── │  │ ──────────── │
│ • column alias   │  │ • LLM map/reduce themes   │  │ • fee lexicon│  │ • live fetch │
│   mapping        │  │   OR documented keyword  │  │ • per-area   │  │   of 5 official│
│ • date/rating    │  │   taxonomy (no LLM key)  │  │   grouping   │  │   groww.in  │
│   parsing        │  │ • 0-100 ranking score    │  │ • evidence   │  │ • token-level│
│ • exact-duplicate│  │ • verbatim quotations    │  │   threshold  │  │   fact check │
│   removal        │  │ • measured counts only   │  │ • transparent│  │ • snapshot   │
│ • 8–12 week      │  └────────────┬─────────────┘  │   failures   │  │   fallback  │
│   window filter  │               │                └──────┬───────┘  └──────┬───────┘
└──┬───────────────┘               │                       │                 │
   │                               └───────────┬───────────┴─────────────────┘
   │                                           │
   │                               ┌───────────▼────────────┐
   │                               │      reports.py        │
   │                               │ • weekly pulse ≤250 w  │
   │                               │ • explanation ≤6 bullet│
   │                               │ • number grounding     │
   │                               │   (unverified numbers  │
   │                               │    are dropped)        │
   │                               └───────────┬────────────┘
   │                                           │
┌──▼───────────────┐               ┌───────────▼────────────┐        ┌─────────────────┐
│  schemas.py      │               │      approvals.py      │        │  integrations/  │
│  Pydantic models │──────────────▶│ • approval request     │───────▶│ • MCP client    │
│  for every model │               │ • content hash         │execute │   (stdio+HTTP) │
│  and tool output │               │ • audit log (JSONL)    │        │ • Notion MCP    │
└──────────────────┘               │ • idempotency ledger   │        │ • Gmail MCP     │
                                   └────────────────────────┘        │ • simulation    │
                                                                    └─────────────────┘
```

### Module responsibilities

| Module | Responsibility |
|---|---|
| `groww_intel/ingestion.py` | Read a CSV with arbitrary column names, normalise dates/ratings/text, remove exact duplicates, apply the 8–12 week window filter, report accepted/excluded/deduplicated counts. |
| `groww_intel/schemas.py` | Pydantic models for every record, model output and external payload. Nothing reaches a report or a tool call unvalidated. |
| `groww_intel/analysis.py` | Theme discovery (LLM map/reduce, or the documented keyword taxonomy when no LLM key is present), transparent 0–100 ranking, verbatim quotation selection with provenance checks. |
| `groww_intel/fee_issue.py` | Fee lexicon, per-product-area grouping, evidence threshold, candidate table, honest "not enough evidence" path. |
| `groww_intel/sources.py` | Fetches the official Groww pages live, verifies each fee fact token-by-token, supports a dated snapshot fallback, records `Last checked`. |
| `groww_intel/reports.py` | Weekly pulse (≤250 words, exactly three recommendations) and customer explanation (≤6 bullets, grounded numbers only). |
| `groww_intel/approvals.py` | Approval request, content hash, approval/rejection, audit log, idempotency ledger that blocks duplicate writes. |
| `groww_intel/integrations/` | MCP client (stdio + streamable HTTP), Notion documentation appender, Gmail draft creator, explicitly labelled simulators. |
| `groww_intel/llm.py` | One OpenAI-compatible client (Groq default, OpenAI supported) with retries, auth/rate-limit errors and JSON extraction. |
| `groww_intel/pipeline.py` | `run_pipeline()` — the single entry point used by the app, the CLI and the tests. |

---

## 3. Data source and collection limitations

### What is collected, and how

* **Source:** the public Apple App Store customer-reviews RSS feed for the Groww
  app (India storefront, App Store id `1404871703`):
  `https://itunes.apple.com/in/rss/customerreviews/page={1..10}/id=1404871703/sortby={mostrecent|mosthelpful}/json`
* **Why this source:** it is a first-party, no-authentication, machine-readable
  feed of verbatim public reviews with a review id, date, rating, title and
  body. No scraping of private APIs, no login, no paywall.
* **Collection script:** `scripts/collect_appstore_reviews.py` — fetches both
  public feeds (10 pages each, polite 1 s delay, retries with backoff),
  de-duplicates by review id and writes
  `data/collected/groww_appstore_reviews.csv` plus a
  `.manifest.json` recording the exact feed URLs, the collection timestamp and
  the date range.
* **Bundled sample:** `data/sample/groww_reviews_sample.csv` is a **real**
  30-review subset of the collected data (charge-related reviews included) so
  the demo runs in seconds. It is *not* synthetic text — see
  `data/sample/README.md`.

### Limitations (stated, not hidden)

1. **Google Play Store reviews could not be collected.** The Play web client
   serves review data only through an authenticated
   `/_/PlayStoreUi/data/batchexecute` RPC that answers `PlayDataError` to
   anonymous requests (verified). The dataset is therefore **iOS-only**, which
   biases it towards iPhone users. Android-only complaints (e.g. specific
   notification or battery issues) are absent.
2. **The "most helpful" feed is not a representative baseline.** It is
   usefulness-ranked, so it over-represents long, strongly-felt reviews. It is
   merged only to widen the recency window; temporal comparisons are therefore
   only reported when both halves of the window contain enough reviews.
3. **App Store reviews are self-selected.** They over-represent extreme
   experiences. Review counts are a measure of *review volume*, not of the
   whole customer base.
4. **No ratings for some themes** — severity falls back to a neutral 0.5 when a
   theme has no rated reviews, and the UI shows the raw score components.
5. **Product area is derived, not authoritative.** `product_area` is inferred
   by the documented keyword rules in `ingestion.py` and flagged as
   `derived_heuristic` in the CSV. Fee findings are additionally re-classified
   with fee-context rules so that, for example, a crude-oil futures fee
   complaint is never pooled with an equity-delivery complaint.

### Why the analysis window is 8–12 weeks

The task requires the most recent 8–12 weeks. The app exposes a slider
(8–12, default 12 = 84 days). Reviews outside the window are **excluded and
counted** (`outside_recency_window`), never silently dropped.

---

## 4. Setup

```bash
git clone https://github.com/sandhiyikasrivastava/groww-feedback-intelligence
cd groww-feedback-intelligence

python -m venv .venv && . .venv/Scripts/activate    # Windows
pip install -r requirements.txt

# 1) local run
streamlit run app.py

# 2) tests
python -m pytest tests -q
```

### Environment variables / secrets

Copy the template and fill it in:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

`secrets.toml` is git-ignored. On Streamlit Community Cloud the same keys go
into **Settings → Secrets** of the deployed app.

| Variable | Required | Purpose |
|---|---|---|
| `LLM_PROVIDER` | no | `groq` \| `openai` \| `none`. Auto-detected from the key below. |
| `GROQ_API_KEY` | no | Enables LLM theme analysis + LLM-written reports. Without it the app runs the **offline deterministic mode** (documented rule-based composer) and labels itself accordingly. |
| `GROQ_MODEL` | no | Default `llama-3.3-70b-versatile`. |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | no | Alternative provider. |
| `NOTION_MCP_URL` | no | Default `https://mcp.notion.com/mcp`. |
| `NOTION_API_KEY` | for live Notion | Notion internal integration token. |
| `NOTION_PARENT_PAGE_ID` | for live Notion | Page the weekly entry is appended to (a child page is created; nothing is overwritten). |
| `GMAIL_MCP_COMMAND` / `GMAIL_MCP_ARGS` | no | Default `npx -y @modelcontextprotocol/server-gmail`. |
| `GMAIL_OAUTH_CREDENTIALS` | for live Gmail | Path to the Google OAuth client JSON. The first call opens a consent screen that a human must complete. |
| `GMAIL_EMAIL_ADDRESS` | no | The mailbox the draft is created in. |

**No key is ever hard-coded, logged or committed.** `.streamlit/secrets.toml`,
`.env*` and `data/state/` are git-ignored.

---

## 5. LLM configuration

`groww_intel/llm.py` speaks the OpenAI chat-completions protocol, so Groq and
OpenAI share one code path.

* **Groq** (default): `https://api.groq.com/openai/v1`, model
  `llama-3.3-70b-versatile`.
* **OpenAI**: `https://api.openai.com/v1`, model `gpt-4o-mini`.
* **No key**: `LLMClient.available` is `False` and the pipeline uses the
  deterministic composer. The UI shows *“Offline deterministic mode”* in the
  sidebar and the reports carry a `mode` field (`llm` / `heuristic`).

### What the LLM is allowed to do

* Theme **names**, **summaries** and **interpretation** — model output.
* Theme **counts**, **ratings**, **dates** — always recomputed from the
  dataset; a model-claimed count is discarded.
* **Quotations** — validated to be an exact substring of a review; a
  non-verbatim quote is replaced or the report is discarded.
* **Numbers in the customer explanation** — every numeric token must appear in
  the verified source text, otherwise the bullet is dropped.

---

## 6. MCP architecture

```
app.py ──► approvals.ApprovalStore  (approval → content hash → audit)
             │
             ├─► integrations.get_documentation_appender()
             │      ├─ NotionDocumentationAppender   → MCP streamable-HTTP
             │      │                                 https://mcp.notion.com/mcp
             │      │                                 tool: post-page / create-pages
             │      └─ SimulatedDocumentationAppender → data/simulated/documentation_entries.jsonl
             │
             └─► integrations.get_email_drafter()
                    ├─ GmailDraftCreator               → MCP stdio
                    │                                   npx @modelcontextprotocol/server-gmail
                    │                                   tool: create_draft   (only)
                    └─ SimulatedEmailDrafter           → data/simulated/email_drafts.jsonl
```

* The MCP transport uses the **official `mcp` Python SDK**
  (`stdio_client` and `streamable_http_client`) — not a REST wrapper wearing
  the MCP name. `tests/test_integrations.py` starts a real local MCP server and
  calls it through this client, so the transport is proven, not assumed.
* **Sending is impossible by construction.** `gmail_draft.py` exposes a single
  allowed tool (`create_draft`); `send_email` is on a forbidden list and a test
  greps the whole package for any send call.
* The email **recipient is optional**. If it is not supplied it is passed as an
  empty string — no address is invented.
* The app **never sends** anything, in any mode.

### Live vs. simulation — the difference

| | Live | Simulation |
|---|---|---|
| Trigger | credentials present in secrets/env | credentials missing |
| Transport | real MCP session to Notion / Gmail | local JSONL file |
| Receipt | `status="success"`, real external id | `status="simulated"`, `simulated=True`, text says *“SIMULATION: …”* |
| UI | green success message | orange warning + red sidebar badge |
| Files | external service | `data/simulated/*.jsonl` |

A simulation is never presented as a successful external write.

---

## 7. Approval workflow

```
 content edited in the UI
          │
          ▼
  ApprovalRequest created   (action, destination, exact content, sha256 hash)
          │
          ├──▶ Reject  ──▶ status=rejected ──▶ execution permanently blocked
          │
          └──▶ Approve ──▶ status=approved
                             │
                             ▼
                    assert_executable()
                      • status must be "approved"      (else NotApprovedError)
                      • content hash must still match  (else re-approval required)
                      • idempotency key must be unseen (else no second write)
                             │
                             ▼
                    external write ──▶ receipt ──▶ audit log + ledger
```

* The approval screen shows the **exact** content, the **destination** and the
  **action** before anything runs.
* Editing the text after approval invalidates the approval
  (`status=invalidated`) and the reviewer must approve again.
* Rejections are final — a rejected request cannot be approved later.
* The audit log (`data/state/audit_log.jsonl`) records created / approved /
  rejected / content_changed / executed events with the content hash.
* The execution ledger (`data/state/execution_ledger.json`) maps
  `sha256(action|destination|content)` → receipt, so a retry cannot write twice.

---

## 8. Evidence thresholds and limits (all enforced in code)

| Limit | Value | Where |
|---|---|---|
| Themes | ≤ 5 | `analysis.py`, `config.max_themes` |
| Quotations | ≤ 3 | `analysis.py` |
| Weekly pulse | ≤ 250 words | `reports.py` (re-composed and re-counted until it fits) |
| Customer explanation | ≤ 6 bullets | `reports.py` |
| Recommendations | exactly 3 | `reports.py` |
| Recency window | 8–12 weeks | `ingestion.py` |
| Fee issue | ≥3 supporting reviews, ≥2 explicit fee mentions, ≥2 distinct weeks, ≥2 distinct authors, one product area | `fee_issue.py` |

If the dataset does not contain a recurring fee confusion, the app says so
explicitly instead of inventing a finding.

---

## 9. Testing

```bash
python -m pytest tests -q
```

68 tests covering:

* CSV validation, column-alias mapping, empty-text and bad-date exclusions
* exact-duplicate removal (and near-duplicates *kept*)
* 8–12 week date filtering, including future dates
* theme cap of five and measured (not model-claimed) counts
* quotation provenance — quotes must exist verbatim in the dataset
* fee-issue evidence threshold, single-author rejection, product-area isolation
* 250-word and 6-bullet limits, number-grounding of the customer explanation
* source verification: verified / uncertain / not-found, snapshot fallback
* approval enforcement: unapproved, rejected, content-changed and duplicate
  executions are all blocked
* failed integrations return a failed receipt instead of raising
* the MCP transport against a **real local MCP server**
* **no automatic email sending exists** (static check of the whole package)

---

## 10. Deliverables in this repository

| Artefact | Location |
|---|---|
| Working prototype (Streamlit) | `app.py` |
| Weekly product pulse (Markdown) | `docs/weekly_product_pulse.md` |
| Customer-facing fee explanation | `docs/customer_fee_explanation.md` |
| Documentation-append evidence | `docs/evidence/documentation_append.md` (+ `data/simulated/documentation_entries.jsonl`) |
| Email-draft evidence | `docs/evidence/email_draft.md` (+ `data/simulated/email_drafts.jsonl`) |
| Review CSV (real, collected) | `data/collected/groww_appstore_reviews.csv` |
| Review CSV (small real sample) | `data/sample/groww_reviews_sample.csv` |
| Source list | `docs/sources.md` |
| Test results | `docs/test_results.md` |
| Architecture & limitations | this README |

---

## 11. Known limitations

1. **iOS-only dataset** (see section 3) — Google Play could not be collected.
2. **No LLM key in the reference run** — the published pulse and explanation
   were produced by the deterministic composer from measured data; the LLM
   path is implemented, wired and tested, and activates automatically when a
   key is added.
3. **Notion/Gmail writes are simulated in the reference run** — the credentials
   are not available in this environment. The MCP code paths are complete and
   the MCP transport is tested against a real MCP server.
4. **Fee fact verification depends on live pages.** If groww.in blocks a
   request (it rejects non-browser clients), the app falls back to a dated
   snapshot and says so in the source table.
5. **The product-area taxonomy is heuristic.** Fee findings are always scoped
   to one area and cross-area reviews are listed separately rather than merged.
6. **The demo tunnel is temporary** (section 1).
