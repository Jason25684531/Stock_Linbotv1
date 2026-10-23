# Optimized Official Data Download Plan — 2026-10-23

Not executed. Owner approval is needed before any batch below runs.

## Why fewer than 3,000 requests
- MI_INDEX (prices, calendar) is already cached for 2021-12-30..2026-07-28 (1105 sessions since 2022): no historical price request is needed.
- T86 and MI_MARGN for 2025-12-15..2026-07-28 and 2026-09-28..10-21 are cached with SHA-256.
- Reuse of the TWSE files needed for 2022..2026-07: 1,399 of 3,315 (42%).
- The retraining question needs only a sample, not the full history.

## Endpoint capabilities (5 probe requests)
- T86: per day only (month query rejected, probe t86_month_query); history from 2012-05-02
- MI_MARGN: per day only (probe mi_margn_month_query)
- MI_INDEX: per day only; cached 2021-12-30..2026-07-28
- TWT49U: date range in one request (cached file TWT49U_20230103_20260728.json)
- TWSE OpenAPI: MI_INDEX / MI_MARGN latest day only, no parameters, no T86 (probe twse_openapi_catalog)
- TPEx OpenAPI: quotes / 3insti / margin latest day only, no parameters (probe tpex_openapi_catalog)
- MOPS monthly revenue: one summary page per market and month, no announcement time (probe mops_monthly_revenue_summary)

No official batch or range download exists for T86 / MI_MARGN / MI_INDEX history; one request per session is the minimum.

## Plans
| Plan | Requests | Hours at 3 s | MB | Purpose |
|---|---:|---:|---:|---|
| A minimal | 44 | 0.04 | 8.4 | measure MySQL foreign/trust accuracy inside the fit split (decides wrong_flows_in_fit) |
| B stage 1 (= A) | 44 | 0.04 | 8.4 | fit-split flow accuracy |
| B stage 2 | 1872 | 1.56 | 293.4 | certified T86 + MI_MARGN 2022-01-03..2025-12-12 (retraining, research) |
| B stage 3 | 153 | 0.13 | 288.1 | MI_INDEX + T86 + MI_MARGN 2026-07-29..2026-10-23 (weekday upper bound) |
| B stages 1-3 | 2069 | 1.72 | 589.9 | |
| C complete | 7469 | — | — | B plus TPEx, MOPS FY2026, monthly revenue pages (stage 4 estimates) |

Stage 4 estimates: TWT49U ranges (2022, after 2026-07-28): 2; MOPS monthly revenue summary pages (2 markets x 57 months, values only): 114; MOPS FY2026Q1-Q2 filings (pit_v3 ledger rate ~890 per quarter): 1780; TPEx quotes + institutional + margin, per day since 2022 (endpoints to be confirmed): 3504

## Recommendation
B: run stage 1 and stage 3 now (on approval); stage 2 only together with an approved retraining; stage 4 per data owner. Stage 1 + 3 = 197 requests (about 10 minutes).

Stage 1 sessions (44): 2022-01-03, 2022-02-07, 2022-03-01, 2022-04-01, 2022-05-03, 2022-06-01, 2022-07-01, 2022-08-01, 2022-09-01, 2022-10-03, 2022-11-01, 2022-12-01, 2023-01-03, 2023-02-01, 2023-03-01, 2023-04-06, 2023-05-02, 2023-06-01, 2023-07-03, 2023-08-01, 2023-09-01, 2023-10-02, 2023-11-01, 2023-12-01, 2024-01-02, 2024-02-01, 2024-03-01, 2024-04-01, 2024-05-02, 2024-06-03, 2024-07-01, 2024-07-18, 2024-08-01, 2024-09-02, 2024-10-01, 2024-11-01, 2024-12-02, 2025-01-02, 2025-02-03, 2025-03-03, 2025-04-01, 2025-05-02, 2025-06-02, 2025-07-01

## Batch procedure (for the approved run)
1. Reuse `scripts/daily_selection_recovery.py` `fetch_raw` (skips cached files, writes URL / retrieval time beside each file) and `parse_chip`; MI_INDEX through `core.research.sources.twse`.
2. One batch per dataset and month; a progress file `progress_<dataset>.json` records each session as OK / NON_TRADING / FAILED(n); a rerun resumes from it.
3. At most 3 attempts per session (3 s, 6 s, 12 s); stop the batch after 5 consecutive failures and report the remaining dates.
4. Validate `stat`, payload date = requested date and schema before keeping a file; mismatches are quarantined, never kept.
5. Official files stay in `artifacts/official_*_cache/` (git-ignored); MySQL is only read for reconciliation; nothing is written to the production DB.
6. After each stage: rebuild the manifest (per-file SHA-256), reconcile per stock and date with `match_table`, and grade DATA_PASS / DATA_FAIL / DATA_BLOCKED.
