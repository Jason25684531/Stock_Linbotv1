# FundamentalDatasetSpec — IMMUTABLE HANDOFF

This file is generated from the final authoritative artifacts by the v3 pipeline.
It is the immutable input contract for `add-fundamental-factor-validation-and-composite-v1`.

## Identity

- `dataset_version`: `fundamental-pit-v3`
- `dataset_revision`: `1`
- `universe_version`: `research_universe.parquet` eligible target freeze
- `target_ticker_count`: `890`
- `target_ticker_sha256`: `d311c1fea8c3110c1d9b940798040b8867d9c2ed5d9eefbd3447bd01d1c87594`
- `schema_version`: `fundamental-pit-v3`

## Raw metrics

Supported and evidence-backed: `REVENUE`, `OPERATING_INCOME`, `EPS`.

Blocked with no synthetic substitute: `NET_INCOME`, `TOTAL_ASSETS`,
`TOTAL_LIABILITIES`, `TOTAL_EQUITY`, `SHARES_OUTSTANDING`, `MARKET_CAP`.

## Reporting basis and derived definitions

- Revenue and Operating Income use cumulative-to-standalone conversion only when all prior quarters are available; incomplete history remains `CUMULATIVE`.
- EPS remains cumulative-as-reported; `EPS_YOY` is same-quarter cumulative YoY.
- Revenue and Operating Income YoY use standalone values or the explicit cumulative fallback metric according to `REPORTING_BASIS`.
- `OPERATING_MARGIN` requires standalone Operating Income and Revenue with a non-zero denominator.
- ROE, ROA, Debt-to-Equity, Equity Ratio, Book-to-Price, Earnings Yield, and Market Cap remain blocked.

## Publication and PIT contract

- Canonical source: `https://doc.twse.com.tw/server-java/t57sb01` official MOPS/TWSE HTML.
- Parser: `parse_mops_publication_html`; invalid, login/block, rate-limit, and error pages are quarantined.
- Publication field: official filing table upload date; `retrieved_at` is metadata only.
- Available date: same trading day only for an explicit timestamp before 13:30 Asia/Taipei; otherwise next canonical TWSE trading date.
- Every accepted record satisfies `period_end < publication_date <= available_date` and is joined backward on `available_date`.

## Coverage and readiness

- `normalized_record_count`: `259054`
- `matrix_row_count`: `764510`
- `PIT_ASOF_SUPPORTED_RANGE`: `2023-01-03 onward`
- `QUALITY_DATA_READY=YES` means the Operating Margin subset only.
- `GROWTH_DATA_READY=YES` means PIT-safe Revenue/EPS growth metrics.
- `VALUE_DATA_READY=NO`, `LEVERAGE_DATA_READY=NO`, `SIZE_DATA_READY=NO`.
- `VALUATION_FACTOR_STATUS=BLOCKED`.

The source-ticker, metric-observation, and factor-matrix-observation denominators are reported separately:

| Metric | Source tickers | Metric observations | Factor-matrix observations |
|---|---:|---:|---:|
| `eps` | 827/890 | 705851/764510 | 106120/764510 |
| `operating_profit` | 827/890 | 705851/764510 | 106120/764510 |
| `revenue` | 827/890 | 705851/764510 | 106120/764510 |
| `operating_margin` | 825/890 | 697712/764510 | 106120/764510 |
| `revenue_yoy` | 824/890 | 692643/764510 | 106120/764510 |
| `operating_income_yoy` | 824/890 | 692643/764510 | 106120/764510 |
| `eps_yoy` | 826/890 | 702227/764510 | 106120/764510 |
| `revenue_cumulative_yoy` | 140/890 | 115704/764510 | 106120/764510 |
| `operating_income_cumulative_yoy` | 141/890 | 116563/764510 | 106120/764510 |

## Missingness and limitations

- Matrix NaN cells and classified missing cells: `1745545`.
- No pre-2023 research-date PIT capability is claimed; pre-horizon publications support only the first in-horizon snapshot.
- Production multiple-filing cases were not observed; revision preservation is covered by synthetic tests.
- The frozen research calendar supports `2023-01-03 onward` for the current research matrix.
- Full expansion uses the frozen current eligible universe; survivorship limitations remain those of the research universe.

## Artifact SHA256

| Artifact | SHA256 |
|---|---|
| `fundamental_records.parquet` | `8f37a17d8d4a47ed9018b4da9eda2d0f266f9c3774630741795ab36cbf29087c` |
| `fundamental_matrix.parquet` | `87f9614fecde2abc9ce02eae4015d31a3aaf1ce4b35872763d63394535e0d4cc` |
| `fundamental_missingness_report.csv` | `2dcab4ef0595e9f2c70d6e3060abdfa201a2d2307833e724aa11366fca281a45` |
| `fundamental_coverage_by_metric.csv` | `4ce8c2a5288de8a5e9d05801ced2d1de8cb7d9acb141049aa5c50dd1464ecbcf` |
| `fundamental_coverage_by_ticker.csv` | `c705e631aefa7fdd66680a42055de95ec77899a360d38f91b45de3fbe8431488` |
| `fundamental_coverage_yearly.csv` | `ff3bce4934028b92f119b878384663c339547539aa961e3a43d8a7ffe198d1a0` |
| `publication_mapping.csv` | `ee0100f5c930f8e1189814285a00b97df5efc0ad70b5742ccf9914cb563587e7` |
| `metric_lineage.csv` | `b9ce584efe4d262c7da589ac00ab5df904f10c10cd02cff93a38e8125ea12412` |
