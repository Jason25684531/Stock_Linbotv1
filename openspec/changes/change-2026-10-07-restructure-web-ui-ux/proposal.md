## Why

The current Flask dashboard collapses market summaries, stock lookup, strategy selection, operational state, and raw API payloads into one tabbed view, while the previously available candlestick workspace is no longer rendered even though the stock-analysis API still returns candles, volume, and moving averages. The Web UI needs a clear page-level information architecture that restores decision-oriented stock analysis without changing Strategy Platform, backtest accounting, runtime gates, persistence, scheduling, or broker safety.

## What Changes

- **BREAKING (presentation only)** Replace the current all-in-one `/dashboard` tab layout with a summary-and-navigation dashboard. Existing public API routes and their field meanings remain compatible.
- Add a responsive authenticated application shell in `base.html` with a fixed desktop sidebar, tablet collapse behavior, mobile drawer, active navigation state, and a truthful `SYSTEM ONLINE` indicator. It must never imply live trading while `BROKER_ORDER_SUBMISSION=DISABLED`.
- Add an independent `/stock-analysis` view, including `/stock-analysis?id=<stock_id>`, that consumes the existing `/api/stock-analysis` contract and renders real Lightweight Charts candlesticks, MA5/MA20/MA60, volume, technical metrics, institutional flow, strategy matches, and explicit normal/degraded/fallback status.
- Add an independent `/strategies` Strategy Center that presents the seven canonical legacy-backed strategies plus the Fundamental canonical identity in one UI catalog while preserving their distinct executors, lifecycle, eligibility, aliases, and capability limits.
- Support cross-category strategy multi-selection for Daily and Backtest actions only where existing backend contracts authorize the action. The UI must not invent Fundamental Daily or Backtest support, lifecycle, aliases, scores, or runtime behavior.
- Retain `/backtest`, `backtest.html`, and `backtest_result.html`; reorganize their presentation and source selectable strategies from the same catalog/capability view without changing the Backtest Engine, accounting, persistence, metrics, CLI, or compatibility wrappers.
- Replace raw JSON as the primary dashboard UI with compact cards, tables, status badges, and navigation. Raw payloads may remain only in an explicitly collapsed development/debug area.
- Preserve existing Flask/Jinja, Tailwind CSS, Alpine.js, Lightweight Charts, Plotly, and Chart.js technology choices; add no SPA framework.
- Add route/template/API ownership evidence and frontend/integration regression coverage, including actual chart construction, degraded local-fallback rendering, canonical/alias presentation, Fundamental no-V-alias behavior, navigation, responsive structure, and broker-disabled state.

## Capabilities

### New Capabilities

- `web-shell-navigation`: Authenticated dark-quant application shell, responsive sidebar, active navigation, and truthful system/broker status presentation.
- `stock-analysis-workspace`: Dedicated stock search and analysis page that renders existing candlestick, moving-average, volume, technical, institutional, strategy-match, and degraded-fallback contracts.
- `strategy-center-ui`: Unified presentation catalog for seven canonical legacy-backed strategies and the Fundamental canonical identity, including lifecycle/capability display and guarded multi-selection actions.
- `backtest-ui-workflow`: Page-level Backtest configuration and result presentation that consumes backend-authorized strategy capabilities without changing accounting or execution semantics.

### Modified Capabilities

- `dashboard-stock-snapshot`: Replace the six detailed lazy-loaded Dashboard tabs with compact market, recommendation, strategy, system, and quick-search summaries that link to dedicated pages.
- `dashboard-signal-resilience`: Preserve non-fatal recommendation degradation in compact Dashboard presentation after removal of the former live-signal/detail-card interaction model.

## Impact

- Primary Web files: `templates/base.html`, `templates/dashboard.html`, new or existing `templates/stock_analysis.html`, new or existing `templates/strategy_center.html`, `templates/backtest.html`, and `templates/backtest_result.html`.
- Flask composition: `app/web_server.py` route inventory and thin presentation adapters only; existing API owners in `app/dashboard_payloads.py`, `core/db_helper.py`, `core/mcp_client.py`, Strategy Registry/Manager, and Fundamental runtime remain authoritative.
- Existing APIs reused: `/api/stock-analysis`, `/api/market/summary`, `/api/market/recommendations`, `/api/market/system-status`, `/api/strategies`, `/api/backtest/run`, and any verified equivalent contracts discovered during implementation.
- Tests: update current Dashboard template contracts; add dedicated page, chart-rendering, navigation, catalog, capability, degraded-fallback, responsive, and integration tests; retain full characterization and regression suites.
- No new frontend framework, DB schema, strategy algorithm, StrategyRunner routing, registry behavior, Fundamental fingerprint/OOS/gate semantics, scheduler owner, broker integration, or backtest accounting change is authorized.
- Existing uncommitted `strategy_settings.json` and `Sample_pic/` content remain user-owned and outside this change.
