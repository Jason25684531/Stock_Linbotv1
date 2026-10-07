# Phase 1 Inventory and Baseline

Date: 2026-10-07

## Route → Template → API → Owner

| Route | Current template/response | API or action | Owner/evidence |
|---|---|---|---|
| `/` | redirect | `url_for('dashboard')` | `app/web_server.py` |
| `/login` | `templates/login.html` | login form | Flask-Login / `app/__init__.py` |
| `/dashboard` | `templates/dashboard.html` | `/api/market/summary`, `/api/market/system-status`, lazy `/api/market/*`, `/api/stock-analysis`; legacy `/update_strategy` and `/update_mode` forms | Web presentation; payloads in `app/web_server.py`, data in `app/dashboard_payloads.py` and `core/db_helper.py` |
| `/backtest` | `templates/backtest.html` and `templates/backtest_result.html` | `POST /backtest` calls `_run_portfolio_backtest`; `POST /api/backtest/run` is JSON equivalent | Web presentation; `core.backtest.runner.PortfolioBacktestEngine` owns execution/accounting |
| `/stock-analysis` | **Absent** | Existing `GET /api/stock-analysis?id=...` | Existing adapter in `app/web_server.py`; health payload owner in `app/dashboard_payloads.py` |
| `/strategies` | **Absent** | Existing `GET /api/strategies` | `StrategyManager` / Strategy Registry |
| `/system` | **Absent** | Existing `GET /api/market/system-status` and `/api/dashboard/health-check` | `pipeline_runs` via `app/web_server.py` / `core.db_helper.py` |
| HTML Trades | **Absent** | Existing `GET /api/trades` only | `app/web_server.py`; no page owner |

Other relevant API routes are `/health` (from `Config.APP_HEALTH_PATH`), `/api/dashboard/health-check`, `/api/market/summary`, `/api/market/recommendations`, `/api/market/snapshot`, `/api/market/institutional`, `/api/market/technical`, `/api/market/margin`, `/api/market/system-status`, `/api/strategies`, `/api/backtest/run`, and `/api/trades`.

## Template and frontend inventory

- `templates/base.html` loads Tailwind CDN, Alpine.js, Chart.js, Plotly, and Lightweight Charts 4.2.3. It currently has a top header with only Dashboard/Backtest links and displays `LIVE`.
- `templates/dashboard.html` uses Alpine `stockSnapshotDashboard`, six lazy-loaded API tabs, a strategy form, a stock health panel, and `<pre>` JSON as the main payload presentation. It does not call `LightweightCharts.createChart`.
- `templates/backtest.html` posts selected strategy keys, dates, weights, and persistence to `/backtest`.
- `templates/backtest_result.html` renders backend-provided metrics, strategy performance, and three Plotly charts. It has no run-specific trades data.
- There is no `static/` frontend asset tree in the repository at inventory time; the relevant JavaScript is inline in templates.
- Existing tests live under `test/`, with pytest configured by `pytest.ini`; Node is already used by existing template JavaScript syntax tests.

## Stock Analysis API contract

`GET /api/stock-analysis` requires `id`, `stock_id`, or `symbol`. It delegates to `_build_dashboard_health_check_payload` and adapts the result to:

```text
status, as_of_date, stock_id, source, warnings, quote,
kline_ma: {candles, volume, ma5, ma20, ma60},
technical, chip: {institutional, summary, chip_flow},
action_script, diagnosis, metadata: {fallback_used, message, price_map}
```

The current backend accepts `period=daily|weekly|monthly` and computes the selected aggregation server-side. It does not expose the requested 1M/3M/6M/1Y/ALL contract. The UI must therefore use the returned daily history and filter browser-side for those display ranges until a verified contract says otherwise.

The health payload marks MCP/local fallback through `fallback_used`, `source`, `warnings`, and `message`. Valid fallback candles remain usable. Strategy-match evidence is not part of the current stock-analysis adapter; the Stock Analysis UI must show Strategy Matches unavailable unless persisted recommendation evidence is explicitly joined by an authoritative backend owner.

## Strategy and capability inventory

`StrategyManager.CANONICAL_REGISTRY` and `STRATEGY_METADATA` contain exactly seven canonical legacy-backed identities:

| Canonical ID | Display alias | Legacy keys |
|---|---|---|
| `hybrid_trend_rank` | V31 | `v31`, `v31_hybrid` |
| `defensive_low_volatility` | V33 | `v33`, `v33_low_vol` |
| `growth_momentum_breakout` | V34 | `v34`, `v34_turbo` |
| `quality_growth` | V35 | `v35`, `v35_innovation` |
| `institutional_flow_confirmation` | V36 | `v36`, `v36_chip_momentum` |
| `mean_reversion` | V37 | `v37`, `v37_mean_reversion` |
| `quality_value_low_volatility` | V38 | `v38`, `v38_value_dividend` |

The Fundamental runtime identity is `fundamental_g2g3_top5_reb60_score_weighted_v1`. It is deliberately outside the legacy registry, has no V alias, reports shadow enabled/live disabled, and preserves `BROKER_ORDER_SUBMISSION=DISABLED`. Its production eligibility is fail-closed and depends on immutable evidence, gates, OOS, and feature flags.

Current `/api/strategies` returns only the seven canonical legacy entries with `name`, `display_name`, type/risk/description when available, and `active`; it has no authoritative Daily/Backtest capability fields. `set_active_strategies()` resolves only the seven legacy canonical/alias identities. The existing Backtest UI receives `StrategyManager.list_strategies()` and therefore also exposes only those seven canonical entries. Capability for Fundamental Daily/Backtest is **UNKNOWN/unsupported at this boundary** and must not be inferred as true.

## Operational evidence inventory

- Flask health (`Config.APP_HEALTH_PATH`) proves Flask and MCP configuration only.
- `/api/market/system-status` reads the latest `pipeline_runs` rows for `update_database`, `run_daily`, and `push_to_line`, mapping missing rows to `Unknown` and aggregate status to `degraded`.
- Market/data evidence comes from `daily_market_data`, recommendation tables, and the existing market envelope APIs.
- Database evidence is indirect through successful DB/pipeline queries; no independent Database health endpoint was found.
- Scheduler evidence is in `jobs/scheduler.py` and `pipeline_runs`; no independent live scheduler heartbeat endpoint was found.
- Strategy Engine and Backtest Engine health endpoints were not found; their presence/capability must remain Unknown unless a verified owner is added.
- Broker state is explicitly `BROKER_ORDER_SUBMISSION=DISABLED` in the Fundamental runtime and existing project contract; this is expected configuration, not an error.

## Baseline evidence

Command:

```text
pytest -q test/test_web_api_smoke.py test/test_dashboard_stock_snapshot.py \
  test/characterization/test_dashboard_payload_baseline.py \
  test/characterization/test_strategy_listing_baseline.py \
  test/characterization/test_strategy_registry_regression.py \
  test/characterization/test_strategy_platform_identity_baseline.py \
  test/characterization/test_web_backtest_persistence_baseline.py \
  test/characterization/test_legacy_cli.py
```

Result: **18 passed, 1 xfailed**, 1 warning in 2.53 seconds on Python 3.11.9 / pytest 9.0.2 (Windows). The xfail is the intentional pre-existing 14-key legacy listing baseline.

The Phase 1 characterization tests in `test/characterization/test_web_ui_restructure_baseline.py` freeze the stable API/identity contracts above without freezing the Dashboard presentation that Phase 3 is authorized to replace.
