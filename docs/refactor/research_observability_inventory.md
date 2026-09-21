# Research Observability Inventory

Inventory date: 2026-09-18. This inventory is based on repository paths and live artifact schemas, not README claims. `outputs/strategy_diagnostics/` is a derived snapshot location and is never an evidence source.

## DATA_SOURCE_MAP

| Source | Provider | Canonical location | Current evidence |
|---|---|---|---|
| Market OHLCV | TWSE / market adapters | `core/research/sources/twse.py`, `core/research/market_data.py`, DB update path | runtime `market_data_refresh_report.json` |
| Financial statements | MOPS | `core/update_financials_mops.py`, `core/update_history_financials.py` | `outputs/fundamental_data/*/financial_*` |
| Publication dates | MOPS | `core/research/fundamental_pit*.py`, `core/research/fundamentals/pit.py` | `publication_records.csv`, `publication_lineage_ledger.csv` |
| Monthly revenue | MOPS | `core/update_monthly_revenue.py` | fundamental/PIT metric lineage |
| Institutional/chip | TWSE crawlers | `core/crawlers/chip_data_scraper.py` | database/source availability only |
| Margin | TWSE update path | `jobs/update_database.py` | database/source availability only |
| Trading calendar / universe | research contracts | `core/research/universe.py`, `core/research/selection/universe.py` | target freeze and runtime manifest |
| Processed PIT research data | Fundamental PIT v3 | `outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z/` | `fundamental_manifest.json`, `pit_validation_report.json` |

## DATA_PIPELINE_MAP

| Pipeline | Entrypoint | Scope | Status evidence |
|---|---|---|---|
| Daily database update | `jobs/update_database.py` | market, financial and related DB refresh | database tables / run output |
| MOPS financial update | `core/update_financials_mops.py` | financial source ingestion | financial records and PIT lineage |
| Monthly revenue update | `core/update_monthly_revenue.py` | monthly revenue ingestion | metric lineage |
| PIT build | `jobs/run_fundamental_pit_v3.py` | publication-aligned fundamentals | PIT manifest and validation report |
| Fundamental factor validation | `jobs/run_fundamental_factor_validation.py` | frozen factor evidence | factor validation artifact |
| Strategy validation | `jobs/run_fundamental_strategy_validation.py` | frozen candidate/robustness | strategy manifest/FrozenStrategySpec |
| Shadow/OOS operation | `jobs/operate_fundamental_shadow.py` | runtime replay and OOS accumulation | current runtime validation manifest and ledger |

## FACTOR_SOURCE_MAP

| Subject | Canonical source | Supporting evidence |
|---|---|---|
| Factor definitions | `core/research/fundamental_factor_validation.py` | `factor_candidate_spec.json` |
| Factor acceptance | `accepted_factor_pool.json` | factor validation manifest, scoreboard/IC/RankIC CSVs |
| Factor metrics | final fundamental validation directory | `factor_ic.csv`, `factor_rank_ic.csv`, `factor_scoreboard.csv`, temporal/redundancy files |
| Existing non-fundamental factors | `core/research/factors/definitions.py` and historical factor artifacts | historical only unless current lineage identifies them |

## STRATEGY_SOURCE_MAP

| Subject | Canonical source | Notes |
|---|---|---|
| Legacy V31/V33–V38 | `core/strategy_manager.py::CANONICAL_REGISTRY` and `LEGACY_STRATEGY_REGISTRY` | code/registry is parameter source; public legacy aliases remain unchanged |
| Frozen Fundamental | `outputs/fundamental_strategy_validation/add-fundamental-strategy-robustness-and-fresh-oos-v1_20260917_v4/FrozenStrategySpec.json` | highest priority for factors/parameters/fingerprint/historical metrics |
| Fundamental runtime binding | `core/runtime/fundamental_shadow.py` and current `FundamentalRuntimeSpec.json` | runtime ID differs from research candidate ID |
| Production boundary | `core/runtime/fundamental_production.py` and current `fundamental_production_status.json` | eligibility is separate from stage |

## VALIDATION_ARTIFACT_MAP

| Validation | Current evidence source |
|---|---|
| PIT/data quality | current runtime `runtime_validation_manifest.json`; PIT `pit_validation_report.json` |
| Factor | final `accepted_factor_pool.json`, factor metrics CSVs |
| Strategy/robustness | frozen strategy validation manifest plus scoreboards/stability/cost/bootstrap/redundancy artifacts |
| Fresh OOS | current operation `FreshOOSAvailabilityReport.json`, `FreshOOSLedger.csv`, `fresh_oos_lineage_audit.json` |
| Runtime/parity | current operation `runtime_validation_manifest.json`, `research_runtime_parity_report.json` |
| Production | current `fundamental_production_status.json`, `promotion_review.json` |

## RUNTIME_STATUS_SOURCE_MAP

The current Fundamental operation root is `outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1/`. Its `runtime_validation_manifest.json` explicitly identifies the strategy fingerprint, PIT/data/factor/parity/Shadow gates, dynamic OOS counts and minima, promotion stage, broker isolation, and failure classification. Its `fundamental_production_status.json` is the production-boundary source for `PRODUCTION_ELIGIBILITY=BLOCKED` and `INSUFFICIENT_OOS`. Historical D5/D6/D7 and earlier Fundamental outputs are archived/superseded unless their fingerprint, dataset version, and declared lineage match the current operation root.

## SOURCE_PRIORITY

1. Frozen strategy specification for frozen parameters and historical metrics.
2. Final validation artifact tied to the same fingerprint/dataset.
3. Current runtime/promotion artifact tied to the runtime strategy ID and fingerprint.
4. Canonical research artifact.
5. Strategy/factor code or configuration.
6. Documentation, for orientation only.

Equal-priority incompatible values are `CONFLICT`; absent/unreadable sources are reported explicitly. Artifact modification time is never used as authority.
