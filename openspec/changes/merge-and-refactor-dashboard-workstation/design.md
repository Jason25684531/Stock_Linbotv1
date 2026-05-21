## Context

The dashboard currently has the newer stock snapshot shell and `/api/market/*` aggregate endpoints, but the modernization removed or obscured two core workflows that existed before: strategy-based stock selection and single-stock health check. The target state is a single dashboard workstation where those core flows remain first-class tabs and the newer market intelligence panels are additive, lazily loaded enhancements.

The project constraints remain Flask + Jinja2 + Alpine.js without a frontend build step. Canonical web routes belong in `app/web_server.py`; root `app.py` remains a facade. Existing daily recommendation behavior should keep using the same persisted recommendation path that feeds `/api/daily-signals` and Line push.

## Goals / Non-Goals

**Goals:**

- Restore `策略選股` as the default tab and keep strategy recommendation data visible with `ai_score`, strategy label, and Z-Score / factor-derived fields when available.
- Restore `個股健檢` as a first-class dashboard tab with a stock-code input and query action.
- Preserve the enhanced tabs for market snapshot, institutional flow, technical strength, margin/short data, and system status.
- Keep tab switching fully client-side through Alpine.js `activeTab` and `x-show`.
- Keep lazy loading: only the default strategy tab and summary cards load on initial render; other tabs fetch on first activation.
- Add or normalize `/api/stock-analysis?id=...` so the stock-health tab has a stable frontend-facing contract.

**Non-Goals:**

- Reintroducing the dashboard equity curve or PK battle widgets into the main dashboard.
- Replacing the standalone `/backtest` page.
- Changing the recommendation scoring model, persistence schema, or Line push behavior.
- Introducing React/Vue, a bundler, or a new frontend dependency.

## Decisions

### 1. Use one Alpine controller for the whole workstation

Keep `stockSnapshotDashboard()` as the single Alpine root and expand its state rather than nesting multiple independent controllers. The controller should own `activeTab`, `loadedTabs`, per-tab loading/error state, strategy query state, and stock-analysis query state.

Rationale: one controller avoids competing `x-data` scopes and makes it harder for a tab to disappear because state is initialized in the wrong scope.

Alternatives considered:
- Multiple nested Alpine controllers: simpler per panel, but riskier for shared tab state and lazy-loading coordination.
- Server-side Jinja tab rendering: safer static HTML, but would lose the required client-side dynamic switching.

### 2. Treat `策略選股` and `個股健檢` as core tabs, not enhanced panels

The tab order should place `策略選股` first and `個股健檢` second, followed by the enhanced market panels. The default `activeTab` should map to the strategy tab.

Rationale: these are the daily user workflows that were over-replaced. Market panels should support decisions, not hide the primary work.

Alternatives considered:
- Keep `每日推薦` as a generic first tab: too vague and does not clearly restore the legacy strategy workflow.
- Put stock health check behind a secondary search control outside tabs: makes the restored workflow easy to miss.

### 3. Build `/api/stock-analysis` as a stable adapter over existing health-check logic

Implement `/api/stock-analysis?id=...` in `app/web_server.py` as a dashboard-specific adapter around existing health-check payload generation where possible. The payload should normalize the fields the frontend needs: quote, K-line/MA series, indicators, institutional/chip context, and next-session action script.

Rationale: the existing `/api/dashboard/health-check` already contains much of the diagnostic logic. A stable adapter prevents the UI from depending on every detail of the older beta payload shape.

Alternatives considered:
- Point the frontend directly at `/api/dashboard/health-check`: fastest, but preserves naming and shape drift from the beta implementation.
- Build a completely separate analysis service: unnecessary duplication and a higher chance of inconsistent stock diagnostics.

### 4. Keep `/api/market/recommendations` aligned with canonical recommendations

The strategy tab should use `/api/market/recommendations` or a shared helper that preserves `/api/daily-signals` semantics: strategy filtering, top N, persisted snapshot metadata, heartbeat/fallback handling, and `ai_score`.

Rationale: recommendation state must remain aligned across dashboard and push channels.

Alternatives considered:
- Query `daily_market_data` directly from the strategy tab: would bypass persisted recommendation fallback and strategy-specific metadata.
- Rebuild strategy scoring in the frontend: not testable, duplicates backend logic, and breaks the lazy-loading contract.

## Risks / Trade-offs

- [Template regression] Inline Alpine scripts can fail silently and blank all panels. Mitigation: add a regression test that renders the template, extracts the inline script, and verifies it compiles.
- [Route duplication] `/api/stock-analysis` could drift from `/api/dashboard/health-check`. Mitigation: implement it as an adapter over shared health-check helpers rather than a second calculation path.
- [Large dashboard template] Restoring core panels increases template size. Mitigation: keep repeated list/table fragments simple and add focused tests before considering extraction.
- [Data gaps] Z-Score details or chip concentration may not exist for every stock/date. Mitigation: return explicit `null`, `empty`, or `degraded` markers while still rendering available quote, K-line, and diagnostic fields.
- [Initial load creep] Restoring core features could accidentally fetch every panel at startup. Mitigation: assert only summary and strategy recommendation endpoints are required for default useful render.

## Migration Plan

1. Add tests that lock the unified tab contract, default strategy tab, stock-health input, lazy-loading endpoint map, and absence of old dashboard-only widgets.
2. Restore the dashboard template structure around the existing top summary cards and a single Alpine root.
3. Implement or normalize `/api/stock-analysis?id=...` in `app/web_server.py`.
4. Keep `/api/market/*` endpoints intact and ensure route names still match the tab endpoint map.
5. Run focused dashboard API/template tests plus existing recommendation fallback and dashboard health-check tests.
6. Roll back by reverting this change only; the existing standalone `/backtest`, `/api/daily-signals`, and `/api/dashboard/health-check` contracts should remain unchanged.

## Open Questions

- Whether `籌碼集中度` should use a direct persisted concentration field when available or a proxy derived from institutional flow and chip score in the first implementation.
- Whether `/api/stock-analysis` should remain a permanent public dashboard API or be treated as a compatibility alias over `/api/dashboard/health-check`.
