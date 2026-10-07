## 1. Phase 1 — Inventory and Characterization Baseline (2026-10-07)

- [x] 1.1 Re-run the Flask route inventory and record each HTML route as `Route → Template → API → Owner`, explicitly identifying absent `/stock-analysis`, `/strategies`, `/system`, and HTML Trades routes before implementation.
- [x] 1.2 Inventory current templates, shared static assets, inline/external JavaScript, chart-library versions, Tailwind/Alpine loading, authentication guards, and responsive conventions.
- [x] 1.3 Inventory `/api/stock-analysis` normal and MCP-failure/local-fallback schemas, including quote fields, candles, volume, MA5/MA20/MA60, timeframe behavior, source metadata, and strategy-match evidence.
- [x] 1.4 Inventory `/api/market/*`, `/api/dashboard/health-check`, and operational evidence for Market Data, Strategy Engine, Backtest Engine, Database, Scheduler, pipeline heartbeat, and broker state; mark unsupported checks Unknown.
- [x] 1.5 Inventory Strategy Manager/Registry metadata, all seven canonical legacy identities and aliases, Fundamental registration/lifecycle/eligibility, Daily settings endpoint acceptance, and every existing `/api/strategies` consumer.
- [x] 1.6 Characterize Backtest-supported strategy identities, request/response schema, metric `None` behavior, run-specific trade availability, charts, accounting, and compatibility entry points before changing shared selection presentation.
- [x] 1.7 Add and run characterization tests for current stock-analysis payload meaning, strategy alias resolution, Fundamental gates, Daily selection rejection/acceptance, and Backtest outputs without changing production behavior.
- [x] 1.8 Record baseline focused and full-suite test results, existing expected failures, current broker-disabled evidence, and unresolved contract limitations in the change evidence.
- [x] 1.9 Create the independently revertible Phase 1 commit containing only inventory evidence and characterization tests after focused verification passes.

## 2. Phase 2 — Shared Shell and Navigation (2026-10-07)

- [x] 2.1 Refactor `templates/base.html` to own the restrained dark-quant theme, JetBrains Mono typography, shared cards/status badges, header, sidebar, and main-content layout using the existing frontend stack.
- [x] 2.2 Add verified links for Dashboard, Stock Analysis, Strategy Center, Backtest, and System with route-based active navigation; do not add Trades without a formal HTML route.
- [x] 2.3 Implement fixed desktop navigation, tablet collapse, and an accessible Alpine.js mobile drawer with keyboard/focus behavior and no page-wide horizontal overflow.
- [x] 2.4 Bind the global system label to verified health evidence, display Unknown/Degraded when evidence is insufficient, remove any `LIVE TRADING` presentation, and show broker submission as expected `DISABLED` configuration.
- [x] 2.5 Add Flask/template tests for shared shell inheritance, all verified navigation targets, exactly one active item, responsive controls, truthful health states, and broker-disabled presentation.
- [x] 2.6 Run Phase 2 focused tests and `git diff --check`, then create an independently revertible Phase 2 commit.

## 3. Phase 3 — Summary Dashboard (2026-10-07)

- [ ] 3.1 Replace the six detailed Dashboard tabs with compact Market, Today's Recommendations, Active Strategies, and System summary cards backed only by authoritative existing fields.
- [ ] 3.2 Add quick stock search that normalizes a verified stock identity, navigates to `/stock-analysis?id=<stock_id>`, and stores recent searches only in browser-local state.
- [ ] 3.3 Render a compact server-ranked recommendation table with canonical date/fallback metadata and links to Stock Analysis, omitting unavailable enrichment without exposing raw JSON.
- [ ] 3.4 Render compact market and system dimensions with explicit Normal/Degraded/Disabled/Unknown/Unavailable states; do not fabricate a TWSE index value or unverified component health.
- [ ] 3.5 Remove the full K-line workspace, complete Strategy table, full Backtest UI, detailed market tabs, and primary raw JSON from Dashboard; keep any required diagnostics collapsed and development-labeled.
- [ ] 3.6 Preserve existing market/recommendation/system API response compatibility even when detailed endpoints are no longer rendered by Dashboard.
- [ ] 3.7 Update Dashboard tests to assert summary content, quick-search routing, degraded/empty recommendation resilience, absence of a large chart/createChart call, and absence of primary raw JSON.
- [ ] 3.8 Run Phase 3 focused tests and `git diff --check`, then create an independently revertible Phase 3 commit.

## 4. Phase 4 — Dedicated Stock Analysis and K-Line (2026-10-07)

- [ ] 4.1 Add the authenticated `/stock-analysis` route and `templates/stock_analysis.html`, supporting an optional validated `id` query parameter without duplicating the existing stock-analysis API.
- [ ] 4.2 Implement stock search/header presentation for authoritative identity, exchange, quote, change, OHLC, volume, turnover, and unavailable values.
- [ ] 4.3 Implement a reusable Lightweight Charts component that calls `createChart`, adds and populates candlestick, MA5, MA20, MA60, and volume histogram series from the existing schema.
- [ ] 4.4 Add 1M/3M/6M/1Y/ALL controls using the verified API timeframe contract or consistent browser filtering of returned history without changing K-line algorithms or field meanings.
- [ ] 4.5 Render valid local fallback candles during MCP failure with `DEGRADED — Local fallback data`; render explicit unavailable state only when no valid candles exist.
- [ ] 4.6 Add responsive chart sizing with a usable minimum height, volume visibility, resize handling, and cleanup of chart/observer instances.
- [ ] 4.7 Render separate technical, institutional, and Strategy Matches cards from authoritative evidence; preserve independent strategy identities and never calculate a unified score.
- [ ] 4.8 If no authoritative persisted strategy-match source exists, render and document an unavailable limitation instead of rerunning strategies or inventing matches.
- [ ] 4.9 Add Flask payload/route tests and a JavaScript DOM test that stubs Lightweight Charts and proves creation/population of candlestick, MA5, MA20, MA60, and volume series for normal and degraded data.
- [ ] 4.10 Run Phase 4 focused tests and `git diff --check`, then create an independently revertible Phase 4 commit.

## 5. Phase 5 — Unified Strategy Center (2026-10-07)

- [ ] 5.1 Implement a backward-compatible presentation catalog assembled from authoritative legacy StrategySpec/Manager metadata and Fundamental registration/eligibility evidence.
- [ ] 5.2 Enrich `/api/strategies` or reuse a verified richer endpoint so catalog rows expose canonical ID, display name, optional aliases, category, lifecycle/eligibility, Daily capability, Backtest capability, and reason text without changing existing fields.
- [ ] 5.3 Add the authenticated `/strategies` route and `templates/strategy_center.html` with All/Technical/Fundamental/Institutional-or-verified-category filters and responsive table/card presentation.
- [ ] 5.4 Show exactly seven legacy-backed canonical strategies plus Fundamental; render V31/V33/V34/V35/V36/V37/V38 only as aliases and prove Fundamental has no V32 or other V alias.
- [ ] 5.5 Implement transient cross-category multi-selection and a sticky action bar that displays selected canonical identities and presentation aliases without merging runtime adapters.
- [ ] 5.6 Gate `套用到 Daily` on all-selected Daily capability, use the existing compatible setter only for accepted identities, and prevent Fundamental from entering the legacy-only settings contract.
- [ ] 5.7 Gate `執行組合回測` on all-selected Backtest capability, pass only supported canonical identities to `/backtest`, and show explicit unsupported/unknown reasons.
- [ ] 5.8 Add API, template, and frontend tests for the eight-entry catalog, categories, cross-type selection, canonical action payloads, alias-only presentation, no Fundamental V32, truthful lifecycle, and both action gates.
- [ ] 5.9 Run existing Strategy Registry/Runner, Legacy adapter, Fundamental fingerprint/OOS/gate, settings, and scheduler compatibility tests to prove runtime semantics are unchanged.
- [ ] 5.10 Run Phase 5 focused tests and `git diff --check`, then create an independently revertible Phase 5 commit.

## 6. Phase 6 — Backtest UI Organization (2026-10-07)

- [ ] 6.1 Update `templates/backtest.html` to source choices from the shared catalog's verified Backtest-capable entries while preserving the existing route, parameters, and engine invocation.
- [ ] 6.2 Keep Fundamental disabled or unavailable with a reason unless Phase 1 proves an existing authoritative Backtest capability; do not add a presentation-only execution path.
- [ ] 6.3 Reorganize `templates/backtest_result.html` into clear Overview, Equity, Drawdown, and Monthly sections or tabs while preserving backend metric and chart values.
- [ ] 6.4 Add a Trades result section only if the current run supplies authoritative run-specific trades; otherwise omit it and record the limitation.
- [ ] 6.5 Verify undefined metrics remain `value=None` with their reason and are never presented as zero or infinity.
- [ ] 6.6 Update Backtest UI/contract tests and run existing accounting characterization to compare trade dates, symbols, directions, order, quantities, and floats at `rtol=1e-9`, `atol=1e-12`.
- [ ] 6.7 Run Phase 6 focused tests and `git diff --check`, then create an independently revertible Phase 6 commit.

## 7. Phase 7 — Frontend and Integration Verification (2026-10-07)

- [ ] 7.1 Add navigation integration tests proving Dashboard, Stock Analysis, Strategy Center, Backtest, and System routes switch correctly and preserve authentication behavior.
- [ ] 7.2 Add responsive structure tests for fixed/collapsible/drawer navigation, horizontally scrollable tables, Strategy Center mobile cards, and chart minimum sizing.
- [ ] 7.3 Add integration coverage from Dashboard stock search/recommendation link through Stock Analysis normal and degraded chart rendering.
- [ ] 7.4 Add integration coverage from Strategy Center selection through Daily and Backtest capability gating, including mixed legacy/Fundamental selections.
- [ ] 7.5 Add status integration tests proving unverified components remain Unknown, broker submission remains Disabled, and the UI never displays `LIVE TRADING`.
- [ ] 7.6 Perform browser-level desktop, tablet, and mobile smoke validation with the repository's existing browser tooling when available, recording screenshots or evidence for the supplied reference's information architecture.
- [ ] 7.7 Run accessibility smoke checks for landmarks, navigation labels, active state, keyboard drawer operation, focus visibility, tables, controls, and status text independent of color.
- [ ] 7.8 Run Phase 7 frontend/integration tests and `git diff --check`, then create an independently revertible Phase 7 commit.

## 8. Phase 8 — Full Regression and UI Review Handoff (2026-10-07)

- [ ] 8.1 Run the full test suite and confirm zero unexpected failures, retaining explicit evidence for any pre-existing expected failures.
- [ ] 8.2 Run focused compatibility regression for `4_run_backtest.py`, `5_push_to_line.py`, legacy aliases, CLI, Web, LINE Bot, `jobs/scheduler.py`, Strategy Registry/Runner, Fundamental gates, DB schema expectations, and broker-disabled state.
- [ ] 8.3 Run `git diff --check` and OpenSpec strict validation for `change-2026-10-07-restructure-web-ui-ux`.
- [ ] 8.4 Review the final diff to confirm no Strategy algorithm, runtime routing, adapter semantics, fingerprint, OOS, promotion gate, DB schema, historical strategy ID, Backtest accounting, scheduler ownership, or broker setting changed.
- [ ] 8.5 Produce the final implementation report covering A Route Map, B New Page Structure, C Modified Files, D Dashboard, E Stock Analysis/K-line, F Strategy Center, G Fundamental integration, H Backtest, I Navigation, J Responsive behavior, K Tests, L Full regression, M Broker state, N Remaining limitations, and O Before/After architecture.
- [ ] 8.6 End the implementation report with exactly `READY_FOR_UI_REVIEW` only if all acceptance evidence passes; otherwise end with `NOT_READY_FOR_UI_REVIEW` and list blockers.
- [ ] 8.7 Create the final independently revertible Phase 8 verification/documentation commit after all required evidence is recorded.
