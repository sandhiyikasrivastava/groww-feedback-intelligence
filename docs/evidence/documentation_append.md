# Evidence — append to the documentation service

- **Action:** append a new entry (nothing is overwritten)
- **Destination:** Simulated documentation service (local JSONL file)
- **Service:** Simulated documentation service (local file)
- **Simulated:** True
- **Execution status:** **simulated**
- **External id:** simulated-doc-5eb5b35385
- **Approval request:** `documentation_append-deb42c977853` (approved by artifact-generator)
- **Content hash:** `deb42c977853eef6194a94376bae6d734ffae401a687bd11c006402b0a790d0c`

## Payload appended

```markdown
## Weekly Product Pulse + Customer Clarification — Groww (2026-10-21)

**Review window:** 2026-07-29 → 2026-10-19 · **Reviews analysed:** 510

### Top themes
- Customer support responsiveness
- App stability, speed and interface
- Account opening, KYC and demat activation

### Weekly pulse

**Window** 2026-07-29 → 2026-10-21 · 510 reviews analysed; theme counts below are measured from the CSV.

**Leading themes**
1. Customer support responsiveness — 25 reviews.
2. App stability, speed and interface — 55 reviews.
3. Account opening, KYC and demat activation — 6 reviews.

**What customers said (verbatim)**
> "In The Indices Chart Of 6M,3M it's actually showing the chart data of 1Month (in iOS)." — review 14613659976, 2026-09-30.
> "After updating App now malfunctioning customer care representative behaviour very rude date 08/10/2026…" — review 14640568430, 2026-10-07.

**Most important observed issue** Brokerage / order execution charge in Stocks (delivery) — 3 reviews, 2 weeks, 3 naming the charge explicitly.

**Recommendations**
1. **Clarify Brokerage Fees and Prevent Unexpected Debits** — Problem: Customers see unexpected ₹5 flat brokerage charge on delivery trades. Fix: Add transparent fee breakdown in order confirmation and post‑trade summary, with alerts for any extra debit. Measure: Reduce brokerage‑related complaint volume to under 5 per month within 3 months.
2. **Accelerate Customer Support Response Times** — Problem: Support responsiveness rated 1.7, with reports of rude behavior and delayed replies. Fix: Implement SLA of 2‑hour first response and mandatory tone‑training for support agents. Measure: Achieve average first‑response time ≤2 hours and support rating ≥4.0 within 2 months.
3. **Show a per-order charge breakdown** — Problem: 3 Stocks (delivery) reviews say per-order charges were higher than expected. Fix: List brokerage, DP, statutory charges and GST on the order confirmation. Measure: Charge-related tickets per 1,000 orders (target -50% in 8 weeks).

### Identified fee issue

Brokerage / order execution charge — Stocks (delivery) (brokerage; a flat ₹5 charge; an unexpected extra debit) — 3 supporting reviews in Stocks (delivery), 2 distinct weeks, 3 explicit fee mentions

### Customer-facing fee explanation

- For each delivery stock order, Groww charges a brokerage of either ₹20 or 0.1 % of the order value, whichever is lower.
- The brokerage cannot be less than ₹5, so many small orders show a flat ₹5 charge.
- This charge is applied when the order is executed (the trade is completed).
- The brokerage amount is calculated on the total value of the buy or sell transaction before taxes.
- An additional 18 % GST is added on the brokerage amount.
- If the calculated brokerage would be below ₹5, the minimum charge of ₹5 is applied, and no lower amount is taken.

### Verified official sources
- https://groww.in/pricing/
- https://groww.in/help/stocks/sx-pricing/how-much-gst-is-charged
- https://groww.in/help/stocks/sx-pricing/what-is-the-charge-for-buy-order-delivery
- https://groww.in/help/stocks/sx-pricing/what-are-intraday-charges
- https://groww.in/calculators/brokerage-calculator/

### Supporting evidence (review ids)
- 14535804011 — groww_appstore_reviews.csv
- 14617217313 — groww_appstore_reviews.csv
- 14618319582 — groww_appstore_reviews.csv

_Execution status: pending_
```

## Receipt

```json
{
  "status": "simulated",
  "external_id": "simulated-doc-5eb5b35385",
  "destination": "C:\\Users\\Asus\\OneDrive\\문서\\Default Project\\groww_feedback_intel\\data\\simulated\\documentation_entries.jsonl",
  "detail": "SIMULATION: the record was appended to a local file instead of a live documentation service. Reason: NOTION_API_KEY is not set — the live Notion MCP server cannot be used.",
  "simulated": true,
  "executed_at": "2026-11-17T12:32:14.983891Z",
  "raw_response": {
    "simulated": true
  }
}
```
