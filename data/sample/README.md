# Bundled sample dataset

`groww_reviews_sample.csv` is a **real** subset of the collected Apple App Store
customer reviews for the Groww app (India storefront) — it is *not* synthetic or
AI-written text. It contains 30 verbatim reviews from the 2026-07-29 → 2026-10-19 window, including the charge-related reviews that
the fee-issue investigation uses.

The full collected dataset lives in `../collected/groww_appstore_reviews.csv` and is
reproduced by `python scripts/collect_appstore_reviews.py`.

Every review keeps its original App Store review id and the public review-page link
so each finding can be traced back to the source.
