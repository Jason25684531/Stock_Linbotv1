# Fundamental Production Integration Inventory

Inventory date: 2026-09-18

This inventory records the implementation paths inspected before the
`complete-fundamental-production-integration-and-operations-v1` change.

## CURRENT_DAILY_FLOW

`jobs/scheduler.py` owns the official pipeline. The daily/evening path currently
executes:

```text
jobs/update_database.py
  -> jobs/run_daily.py
  -> jobs/run_daily_backtest_validation.py
  -> jobs/push_to_line.py --time evening
```

`jobs/run_daily.py::run_daily_for_date()` loads the persistence strategy set
from `StrategyManager`, builds the market/financial feature frame, runs each
legacy strategy, and persists through
`_persist_strategy_recommendations()` into `daily_recommendations`.

## CURRENT_STRATEGY_REGISTRY

- Canonical legacy registry: `core/strategy_manager.py::CANONICAL_REGISTRY`.
- Legacy compatibility registry: `StrategyManager.LEGACY_STRATEGY_REGISTRY`.
- Public listing: `StrategyManager.list_strategies()` / `list_canonical_strategies()`.
- Fundamental runtime identity already declared separately as
  `fundamental_g2g3_top5_reb60_score_weighted_v1`.
- Existing Fundamental Shadow adapter: `core/runtime/fundamental_shadow.py`.
- Frozen identity: `cb7c0d88e58533123d9622315464e82be4a6de636264ba17c0efa4e73c31cc5f`.

The Fundamental ID is intentionally not a legacy strategy class and is not
currently added to the seven-item user-facing strategy list.

## CURRENT_RECOMMENDATION_SCHEMA

The authoritative persistence layer is `daily_recommendations`, written by
`jobs/run_daily.py::_persist_strategy_recommendations()` and read by
`core/db_helper.py`:

```text
stock_id, trade_date, strategy, close_price, ai_score, rsi, volume,
news_boost_reason
```

`core.db_helper.get_recommendations_with_market_fallback()` provides the shared
same-day/strategy-only fallback and heartbeat semantics used by Web and LINE.
No Fundamental-specific table exists or is required.

## CURRENT_SCHEDULER_FLOW

`jobs/scheduler.py` is the only scheduler owner. Fundamental jobs are already
available as explicit targets:

- `jobs/runtime/fundamental_shadow.py`
- `jobs/advance_fundamental_pit_shadow.py`
- `jobs/accumulate_fresh_oos_and_promotion.py`
- `jobs/operate_fundamental_shadow.py`

They are not currently part of `PIPELINES['daily']` or `PIPELINES['evening']`.

## CURRENT_WEB_FLOW

- `app/web_server.py::api_daily_signals()` accepts a strategy key and resolves
  persisted rows through `app._load_strategy_candidates()`.
- `app/web_server.py::api_market_recommendations()` reads
  `app_pkg.get_daily_recommendations()`.
- `app/__init__.py::get_strategy_recommendation()` resolves the selected legacy
  strategy and uses the shared persisted resolver.

The Web reader therefore already understands the canonical persisted schema,
but the Fundamental ID needs a small blocked/eligible display boundary because
it is not a legacy `BaseStrategy` instance.

## CURRENT_LINE_FLOW

- `jobs/push_to_line.py` resolves rows with
  `get_recommendations_with_market_fallback()`.
- `app/line_flows.py` and `app/__init__.py` use the same persisted reader for
  interactive strategy recommendations.
- `app/line_flows.py::_list_strategy_picker_options()` uses the seven-item
  `StrategyManager.list_strategies()` result.

Existing V31/V33-V38 LINE behavior must remain unchanged; Fundamental output
must enter only through the same canonical persisted reader when eligible.

## CURRENT_FUNDAMENTAL_FLOW

```text
FrozenStrategySpec.json
  -> core/runtime/fundamental_shadow.py::FrozenStrategyLoader
  -> CanonicalSelectionAdapter (canonical PIT + research functions)
  -> Shadow selection/health artifacts
  -> core/runtime/fundamental_advancement.py
  -> core/runtime/fresh_oos_promotion.py
  -> FreshOOSAvailabilityReport.json + promotion_review.json
```

Current authoritative operation root:
`outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1`.

Current verified state:

```text
PIT_REFRESH=PASS
DATA_FRESHNESS_GATE=PASS
FACTOR_HEALTH_GATE=PASS
RESEARCH_RUNTIME_PARITY=PASS
RUNTIME_REPLAY=PASS
HISTORICAL_SELECTION_DRIFT=0
FRESH_OOS_TRADING_DAYS=37
FRESH_OOS_MONTHS=3
FRESH_OOS_REBALANCES=1
FRESH_OOS_STATUS=INSUFFICIENT_DATA
PRODUCTION_PROMOTION_STATUS=SHADOW_APPROVED
PRODUCTION_READY=NO
BROKER_ORDER_SUBMISSION=DISABLED
```

The integration boundary must consume this evidence and continue to fail
closed until the frozen Fresh OOS minima are met.
