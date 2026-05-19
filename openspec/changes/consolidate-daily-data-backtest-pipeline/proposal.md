## Why

`stabilize-daily-recommendation-pipeline` locked the current runtime contract, but it intentionally stopped at stabilization. The repo now has a clear official path, yet the operational picture is still fragmented across canonical jobs, compatibility launchers, dashboard readers, backtest outputs, and partial observability.

The next risk is not scheduler ownership confusion alone. It is operational correctness:

- price values can appear current without enough date or source context
- daily freshness is inferred from logs and side effects instead of explicit run state
- backtest execution exists, but daily validation is not integrated into the official scheduler path
- cleanup candidates exist, but removal would still be risky without evidence-based classification

This change builds directly on `stabilize-daily-recommendation-pipeline` and keeps its contracts intact:

- `jobs/scheduler.py` remains the official scheduled entrypoint
- the canonical pipeline remains `jobs/update_database.py` -> `jobs/run_daily.py` -> `daily_recommendations` -> `/api/daily-signals` and Line push
- `daily_recommendations` remains the persisted recommendation contract
- `MODEL_PATH`, `DB_URL`, and `/health` remain the public runtime contracts already established

Price correctness is the first operational priority because a stale or mis-sourced price can make the dashboard, recommendation display, and validation results look healthy while being materially wrong. Daily data freshness also must become observable through persisted state rather than guessed from console output. Lightweight daily backtest validation is valuable as an operational safety check, but it must stay bounded and must not turn the scheduler into a full research engine. Cleanup likewise needs a staged governance process so the repo becomes simpler without breaking hidden workflows.

## What Changes

- Add a proposal-only OpenSpec package for consolidating the official daily data and recommendation pipeline after stabilization.
- Keep `jobs/scheduler.py` as the only official daily pipeline owner and define how future implementation classifies other entrypoints as active path, legacy compatibility, or removable after verification.
- Define a future observability contract for scheduled runs so operators can answer which step ran, which step failed, what trade date was loaded, how many rows changed, and whether recommendation generation, lightweight backtest validation, and Line push completed.
- Define a price-correctness contract that distinguishes latest actual close, raw close, adjusted close, trade date, source date, and ingestion timestamps instead of silently collapsing them into one displayed price.
- Add a future track for lightweight scheduler-safe daily backtest validation after recommendation generation, without expanding daily automation into full historical research or optimization runs.
- Define a staged cleanup governance process for duplicate scripts and ambiguous flows, with evidence requirements before any removal.

## Impact

- Daily operations stay anchored to the existing repo topology instead of introducing a second scheduler or a new production-only stack.
- Data freshness becomes auditable from persisted run state instead of inferred from logs, ad hoc CLI output, or downstream symptoms.
- Dashboard and API consumers gain a future contract for visible price provenance so stale or fallback prices are not silently presented as fresh.
- Backtest automation becomes an operational validation layer for broken data or logic, not a replacement for manual research backtesting.
- Cleanup work becomes safer because deprecation, doc redirects, guardrail tests, and verification all happen before deletion.

## Capabilities

- `pipeline-consolidation`: centralize ownership on `jobs/scheduler.py`, classify surrounding entrypoints, and keep docs aligned with the official path
- `data-freshness`: persist observable run state for daily scheduler execution and step outcomes
- `price-correctness`: make price basis and date provenance explicit across ingestion, persistence, display, and validation
- `backtest-automation`: add optional lightweight daily validation after recommendation persistence, with bounded runtime and persisted status
- `cleanup-governance`: inventory duplicate or unused flows and remove only after evidence-backed verification
