# Official sources used for fee verification

**Last checked: 2026-10-21**

| # | Source | URL | Fetch | Facts verified |
|---|--------|-----|-------|-----------------|
| 1 | Groww — Brokerage Charges & Pricing | https://groww.in/pricing/ | live | equity_brokerage, dp_charges, stamp_duty, stt, account_maintenance |
| 2 | Groww Help — What is the charge for buy order (delivery)? | https://groww.in/help/stocks/sx-pricing/what-is-the-charge-for-buy-order-delivery | live | delivery_sell_charges |
| 3 | Groww Help — What are intraday charges? | https://groww.in/help/stocks/sx-pricing/what-are-intraday-charges | live | intraday_minimum, auto_square_off |
| 4 | Groww Help — How much GST is charged? | https://groww.in/help/stocks/sx-pricing/how-much-gst-is-charged | live | gst |
| 5 | Groww — Brokerage Calculator | https://groww.in/calculators/brokerage-calculator/ | live | brokerage_calculator |

## Fee facts and their verification

| Fact | Value | Status | Source |
|------|-------|--------|--------|
| Equity brokerage (delivery & intraday) | ₹20 or 0.1% of the order value per executed order, whichever is lower, with a minimum of ₹5. | **verified** | https://groww.in/pricing/ |
| DP charges (per sell transaction) | On each sell transaction: depository charge ₹3.50 (male) / ₹3.25 (female) plus Groww's ₹16.50; Groww's fee is waived when the debit value is below ₹100. | **verified** | https://groww.in/pricing/ |
| GST on charges | 18% GST applies on brokerage, DP charges, exchange transaction charges, IPFT, SEBI turnover charges and other listed charges. | **verified** | https://groww.in/help/stocks/sx-pricing/how-much-gst-is-charged |
| Stamp duty (delivery buys) | 0.015% of the buying value for delivery purchases (₹0 on sales). | **verified** | https://groww.in/pricing/ |
| STT (securities transaction tax) | 0.1% of the transaction value on delivery buys and sells; 0.025% on intraday sells. | **verified** | https://groww.in/pricing/ |
| Exchange transaction, SEBI turnover and IPFT charges | Exchange transaction charge 0.00297% (NSE) / 0.00375% (BSE); SEBI turnover charge 0.0001%; IPFT charge 0.0001% (NSE). | **uncertain** | https://groww.in/pricing/ |
| Account maintenance & opening | ₹0 account opening (including trading) and ₹0 account maintenance charges. | **verified** | https://groww.in/pricing/ |
| Minimum brokerage rule for intraday | If brokerage for an intraday trade works out below ₹5, the minimum charged is the lower of ₹5 or 2.5% of the trade value. | **verified** | https://groww.in/help/stocks/sx-pricing/what-are-intraday-charges |
| Auto square-off charge | ₹50 per open intraday position that the system squares off, plus GST. | **verified** | https://groww.in/help/stocks/sx-pricing/what-are-intraday-charges |
| Delivery sell-side charges | On a delivery sale, DP charge is ₹3.50 (male) / ₹3.25 (female) depository charge plus ₹16.50 Groww charge (₹0 Groww fee when the debit value is below ₹100), plus GST; stamp duty is 0.015% of the buying value on buys and ₹0 on sells; STT is 0.1% of the transaction value on delivery buys and sells. | **verified** | https://groww.in/help/stocks/sx-pricing/what-is-the-charge-for-buy-order-delivery |
| Official brokerage calculator | Groww publishes a brokerage calculator so customers can estimate the charges on a trade before placing it. | **verified** | https://groww.in/calculators/brokerage-calculator/ |

## Unverified (excluded from the customer explanation)

- Exchange transaction, SEBI turnover and IPFT charges

## Notes

- All URLs are first-party `groww.in` pages fetched live by `groww_intel/sources.py`; no blogs or third-party claims are used.
- A fact is reported as verified only when its wording was found in the fetched page text (token check, whitespace-insensitive).
