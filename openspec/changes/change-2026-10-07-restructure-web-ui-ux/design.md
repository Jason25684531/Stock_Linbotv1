## Context

The authenticated Web UI is a Flask/Jinja application styled with Tailwind CSS and enhanced with Alpine.js, Lightweight Charts, Plotly, and Chart.js. The current `/dashboard` template concentrates unrelated workflows into one tabbed page and exposes raw payloads as primary UI. A prior dashboard implementation rendered candlesticks, but the current template no longer calls `LightweightCharts.createChart` even though `/api/stock-analysis` still returns candles, volume, MA5, MA20, and MA60. MCP failures currently degrade to local data, so chart rendering must remain available while clearly identifying fallback data.

The Strategy Platform has two intentionally separate runtime families. Seven legacy-backed strategies have canonical IDs, presentation aliases V31/V33/V34/V35/V36/V37/V38, and production lifecycle metadata. `fundamental_g2g3_top5_reb60_score_weighted_v1` has no V alias and has independent shadow/live eligibility and gate semantics. The current `/api/strategies` exposes only the seven legacy-backed entries and does not provide an authoritative action-capability contract. `set_active_strategies()` accepts only the legacy-backed identities. The Backtest UI and engine currently support those same legacy-backed strategies. UI unification therefore cannot imply runtime unification.

The route inventory at proposal time is:

| Route | Template or response | Primary API dependencies | Owner |
|---|---|---|---|
| `/dashboard` | `dashboard.html` | `/api/market/summary`, `/api/market/recommendations`, `/api/market/system-status`, `/api/strategies` | Web presentation |
| `/stock-analysis` | new `stock_analysis.html` | existing `/api/stock-analysis` | Web presentation; existing stock payload owner remains authoritative |
| `/strategies` | new `strategy_center.html` | backward-compatible enrichment of `/api/strategies` or a verified equivalent | Web presentation; Strategy Registry/Manager and Fundamental registration remain authoritative |
| `/backtest` | existing `backtest.html`, `backtest_result.html` | `/api/backtest/run` and existing server-rendered workflow | Web presentation; existing Backtest Engine remains authoritative |
| `/system` | new `system.html` if detailed status evidence is sufficient | `/api/market/system-status`, `/api/dashboard/health-check`, or verified equivalents | Web presentation; pipeline/health owners remain authoritative |

There is an `/api/trades` endpoint but no formal HTML Trades route. No Trades navigation destination is introduced by this change. The supplied visual reference is design guidance, not evidence that missing routes, values, statuses, or capabilities exist.

## Goals / Non-Goals

**Goals:**

- Split Dashboard, stock analysis, Strategy Center, Backtest, and detailed System concerns into independent views under one responsive dark-quant shell.
- Keep `/dashboard` concise and decision-oriented: summaries, compact recommendations, quick stock search, system state, and links to deeper pages.
- Restore actual candlestick, MA5/MA20/MA60, and volume rendering from the existing stock-analysis payload, including degraded local fallback.
- Present all seven legacy-backed canonical strategies and the Fundamental canonical strategy in one catalog without hiding truthful lifecycle, eligibility, aliases, or action limitations.
- Permit cross-category selection in UI state while enabling Daily and Backtest actions only when backend-owned capability evidence authorizes every selected strategy.
- Preserve the existing Backtest Engine and accounting while improving configuration and results presentation.
- Add testable route, template, frontend chart, degraded-data, catalog, capability, navigation, responsive, and regression contracts.

**Non-Goals:**

- Rewriting Strategy Platform, StrategyRunner, StrategyRegistry, LegacyStrategyAdapter, FundamentalRuntimeAdapter, strategy algorithms, fingerprints, OOS evaluation, promotion gates, or scheduler ownership.
- Changing DB schema, historical strategy IDs, backtest accounting, CLI compatibility, LINE Bot behavior, or compatibility entry points.
- Enabling broker order submission or displaying `LIVE TRADING`; `BROKER_ORDER_SUBMISSION=DISABLED` remains unchanged and is not an error.
- Introducing React, Vue, Next.js, another frontend framework, or a separate frontend build architecture.
- Recomputing strategy matches in the browser, summing scores across strategies, or inventing a unified score.
- Creating a fake Trades page, market index value, strategy lifecycle, system health, or Daily/Backtest capability without authoritative evidence.

## Decisions

### 1. Use a shared server-rendered application shell

`base.html` will own the global dark theme, desktop sidebar, tablet collapse control, mobile drawer, active-link treatment, shared status badge primitives, and the right-side `SYSTEM ONLINE` header label. Child templates will provide page title and content blocks. The online badge must derive from verified health evidence; when that evidence is not available, it must use an Unknown/Degraded presentation rather than falsely claiming normal operation. Broker state appears as `DISABLED`, never as an error or live-trading claim.

This keeps navigation and responsive behavior consistent without introducing an SPA router. Duplicating navigation in each page was rejected because it creates drift. A client-side framework was rejected because it violates the established stack and is unnecessary for page-level navigation.

### 2. Keep routes thin and preserve API field meanings

The implementation begins with a fresh route/template/API/owner inventory. New HTML routes may render Jinja templates, and existing presentation APIs may be enriched additively when required. Existing keys and meanings remain compatible. Business calculations stay with their current backend owners; templates and Alpine components only transform authoritative values for display.

This design favors existing endpoints over route duplication. Rewriting the stock-analysis algorithm or Strategy Platform for UI convenience was rejected because it risks semantic and compatibility regressions.

### 3. Make Dashboard a summary and routing surface

`dashboard.html` will contain compact summary cards, quick search, a compact recommendation table, market summary, strategy count/summary, and system summary. It will not instantiate a full candlestick chart, complete Strategy catalog, Backtest workflow, detailed market tabs, or large raw JSON region. Detailed existing market APIs may remain available for compatibility even when they are no longer rendered on Dashboard.

Quick search navigates to `/stock-analysis?id=<normalized-id>`. Recent searches are browser-local UI convenience data, avoiding a DB schema change. If a current market summary API cannot prove the exact TWSE index number, the UI shows an unavailable state or a supported metric rather than inventing the example value.

Keeping the existing all-in-one tabs was rejected because it conflicts with the requested information architecture and makes the initial page expensive and difficult to scan.

### 4. Render the stock chart from the existing contract

`stock_analysis.html` will search by stock ID/name, consume `/api/stock-analysis`, and create a Lightweight Charts chart with a candlestick series, MA5/MA20/MA60 line series, and volume histogram. It will support the existing timeframe contract if present; otherwise it will filter the returned history for 1M/3M/6M/1Y/ALL without changing backend candle semantics. The chart container keeps a usable minimum height and responds to width changes.

Payload normalization is limited to adapting the repo's actual schema to Lightweight Charts inputs. An MCP failure with valid local candles renders the chart and a `DEGRADED — Local fallback data` state. Missing or invalid candles produce a clear unavailable state, not an exception or empty JSON dump.

Strategy matches use persisted recommendation evidence or another verified existing source. Each matching strategy remains an independent row; scores are not added or normalized into a synthetic score. If no authoritative match source exists, the panel explicitly states that matches are unavailable until the contract is added.

Plotly or Chart.js were not selected for candlesticks because Lightweight Charts is already loaded and is the established financial-series dependency.

### 5. Build one presentation catalog over separate runtime owners

The Strategy Center catalog will include the seven canonical legacy-backed strategies plus `fundamental_g2g3_top5_reb60_score_weighted_v1`. Canonical ID is the identity; V numbers are optional display aliases only. Fundamental has no V32 or other V alias. Category, lifecycle, eligibility, and action capability come from backend-owned metadata.

`/api/strategies` may be enriched backward-compatibly with fields such as canonical ID, aliases, category, lifecycle, `available_for_daily`, `available_for_backtest`, and reason text, but these values must be assembled from authoritative existing owners or characterized support. Unknown capability is represented as unavailable/unknown with an explanation. Presentation code must not hardcode Fundamental as Production or supported.

The Strategy Center permits any cross-category combination in transient UI selection. Before an action, it resolves every selected item against that action's capabilities. The action is enabled only if the full selected set is supported by the target backend contract; otherwise the UI identifies unsupported selections. In particular, Fundamental must not be written to `strategy_settings.json` through the legacy-only Daily setter and must not be submitted to the legacy Backtest Engine without proven support.

A merged runtime adapter and a lowest-common-denominator catalog were rejected: the former violates runtime boundaries, while the latter hides a real strategy from discovery.

### 6. Keep Backtest as a thin workflow over the current engine

`/backtest`, `backtest.html`, and `backtest_result.html` remain the public workflow. Configuration obtains its strategy choices from the same catalog/capability presentation used by Strategy Center but submits only entries authorized for Backtest. Existing period inputs, execution route, metrics, and accounting remain unchanged. Results may be organized into Overview, Equity, Drawdown, Monthly, and Trades tabs only when each tab has run-specific evidence. A Trades tab must not reuse global persisted trades as though they came from the current run.

A new Backtest Engine or client-side accounting was rejected because the change is presentational and accounting behavior is a compatibility boundary.

### 7. Treat status as evidence, not decoration

Compact Dashboard and detailed System views map existing health and pipeline evidence to Normal, Degraded, Disabled, Unknown, Not Run, Running, or Failed. `Broker Order Submission = DISABLED` is expected configuration and does not make overall system health red. Components that lack a current backend check must not default to Normal.

The desired six-component system list is therefore conditional on real evidence. Adding thin presentation aggregation is allowed; inventing checks or operational ownership is not.

### 8. Test behavior at contract and browser boundaries

Flask tests will verify routes, templates, backward-compatible API fields, capability gating, and degraded stock payloads. A JavaScript DOM test (jsdom or the repository's verified equivalent) will stub Lightweight Charts and assert calls that create the chart, candlestick series, three MA series, and volume series after candles arrive. Browser-level validation will be used if the repository already supports it or it can be added without changing the frontend architecture.

Characterization baselines precede changes to shared route/catalog/backtest surfaces. Regression scope includes existing Dashboard APIs, strategy aliases, Fundamental eligibility/gates, Backtest, CLI, scheduler, compatibility wrappers, `git diff --check`, and OpenSpec strict validation.

Snapshot-only HTML assertions were rejected as insufficient because a chart container can exist while no chart is ever constructed.

## Risks / Trade-offs

- **[Catalog unification can imply unsupported runtime parity]** → Show per-action capability and reason fields, gate the complete selected set, and retain separate runtime owners.
- **[Existing strategy API lacks lifecycle and capability fields]** → Add only backward-compatible presentation fields derived from authoritative metadata; characterize existing consumers first.
- **[Fundamental cannot currently use legacy Daily or Backtest paths]** → Keep it selectable for comparison/catalog UX but block unsupported actions with explicit reasons; do not persist it through legacy settings.
- **[System endpoints do not prove every desired component is Normal]** → Display Unknown/Unavailable for unverified checks and document the limitation instead of hardcoding green states.
- **[MCP outages could be mistaken for total data loss]** → Render valid local candles with a degraded badge and fallback source text.
- **[Responsive charts can collapse or leak observers]** → Enforce minimum height, resize through the chart API, and clean up observers/chart instances when components are replaced.
- **[Dashboard simplification could remove useful access]** → Preserve API compatibility and provide links from summaries to dedicated pages; place optional raw payloads only in collapsed debug sections.
- **[A result Trades tab could show unrelated records]** → Render it only from run-specific result data; otherwise omit it and record the limitation.
- **[Large template changes could break compatibility entry points]** → Deliver in independently revertible phases and run focused verification after each phase.

## Migration Plan

1. Capture route, template, API, JavaScript, strategy-selection, health-status, and Backtest capability inventory plus characterization baselines.
2. Introduce the shared shell and navigation while preserving existing routes and endpoints.
3. reduce Dashboard to summary content and links; retain APIs for compatibility.
4. Add the dedicated stock-analysis route/template and verify normal and degraded chart rendering.
5. Add the unified Strategy Center catalog and capability-gated selection actions without changing runtime adapters.
6. Reorganize Backtest configuration/results and share only the catalog's proven Backtest-capable entries.
7. Add focused frontend, Flask, integration, responsive, and browser validation.
8. Run full regression, strict OpenSpec validation, and prepare the required A–O implementation report ending in `READY_FOR_UI_REVIEW` or `NOT_READY_FOR_UI_REVIEW`.

Each phase must be independently revertible. Rollback restores the prior templates/routes for that phase while leaving public API contracts and backend runtimes unchanged. No DB migration is required.

## Open Questions

- Which existing backend evidence, if any, can authoritatively report Market Data, Strategy Engine, Backtest Engine, Database, and Scheduler independently? Until confirmed, unsupported rows remain Unknown.
- Does the current stock-analysis API expose an authoritative timeframe contract, or should the browser filter the returned 360-point history for 1M/3M/6M/1Y?
- Which persisted recommendation source can provide per-stock strategy matches without recomputation, and what date/fallback metadata accompanies it?
- Should `/api/strategies` be enriched in place or should an existing richer catalog endpoint be reused after implementation-time inventory? The selected solution must preserve current consumers.
- Is run-specific trade data already available to `backtest_result.html`? If not, the proposed Trades result tab remains omitted rather than showing unrelated data.
- Is the detailed `/system` page supported by enough existing evidence in the first delivery, or should navigation lead to a truthful limited System view while missing checks are documented?
