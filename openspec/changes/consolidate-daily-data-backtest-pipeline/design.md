## Context

The repository already has a stabilized canonical daily path:

`jobs/update_database.py` -> `jobs/run_daily.py` -> `daily_recommendations` -> `/api/daily-signals` and Line push

`jobs/scheduler.py` already documents and executes that path. `execution/daily_run.bat` also points at `jobs/scheduler.py daily`. At the same time, the repo still contains legacy numeric launchers, older batch entrypoints, and separate repair tooling such as `jobs/backfill_pipeline.py`. The design task is to consolidate operational understanding around the existing topology, not replace it.

Current evidence from the repo also shows:

- `daily_market_data`, `daily_recommendations`, `backtest_trades`, and `backtest_equity_curve` already exist as meaningful persistence surfaces
- dashboard aggregation payloads already use `requested_date` and `as_of_date` metadata patterns
- most user-facing price displays still rely on `close_price` plus `trade_date`, with limited explicit price-basis labeling
- the repo has no existing persisted scheduler run-state mechanism analogous to `pipeline_runs`

## Goals

- Keep the stabilized runtime contract intact
- Preserve one official scheduled daily path
- Make daily freshness observable through persisted run state or an existing equivalent mechanism
- Treat price correctness and date provenance as P0 operational requirements
- Add a scheduler-safe daily validation backtest track without turning the scheduler into a research platform
- Establish evidence-based cleanup governance for ambiguous or duplicate flows

## Non-Goals

- Creating a second scheduler path
- Replacing `jobs/scheduler.py` with another scheduler framework
- Introducing broker-backed simulation or real-money execution
- Introducing a production-only topology such as `prod.docker-compose.yml` or `web/Dockerfile`
- Replacing `DB_URL` with structured DB env vars
- Migrating `MODEL_PATH` away from `ML_Data/pkl/stock_ai_model.pkl`
- Expanding the daily scheduler into full historical optimization or expensive research backtests

## Flow Sketch

```text
jobs/scheduler.py daily
        |
        v
+-------------------+
| update_database   |
| daily market data |
+-------------------+
        |
        v
+-------------------+
| run_daily         |
| recommendations   |
+-------------------+
        |
        +---------------------------+
        |                           |
        v                           v
+-------------------+      +-------------------+
| optional daily    |      | push/read         |
| lightweight       |      | consumers         |
| backtest validate |      | /api/daily-signals|
+-------------------+      | Line push         |
        |                  +-------------------+
        v
  persisted run state
  + price/date provenance
```

## Decisions

### Decision: `jobs/scheduler.py` remains the only official daily pipeline entrypoint

The repository already centralizes scheduled execution in `jobs/scheduler.py`, and this change keeps that ownership explicit. No second scheduler path will be introduced. Compatibility launchers may remain temporarily, but they must be classified relative to the official scheduler rather than treated as peers.

Locked contract:

`jobs/update_database.py` -> `jobs/run_daily.py` -> `daily_recommendations` -> `/api/daily-signals` and Line push

`jobs/backfill_pipeline.py` remains repair tooling, not the official scheduled owner.

### Decision: consolidation is classification first, deletion later

The implementation will classify scripts and flows into:

- active path
- legacy compatibility
- removable after verification

Likely active-path examples from current repo topology:

- `jobs/scheduler.py`
- `jobs/update_database.py`
- `jobs/run_daily.py`
- `jobs/push_to_line.py`
- `execution/daily_run.bat`

Likely legacy-compatibility candidates that need evidence review first:

- `1_update_database.py` through `6_optimize_params.py`
- `execution/evening_run.bat`
- `execution/morning_run.bat`
- `scripts/twse_mcp_server.py`

The proposal does not delete or redirect anything yet.

### Decision: daily freshness must be persisted, not inferred

The repo currently exposes market validity through `get_valid_market_dates()`, recommendation completeness through `get_completed_recommendation_strategy_days()`, and dashboard cache freshness through `requested_date` / `as_of_date`. What is still missing is a first-class persisted run-state record for the scheduler itself.

Future implementation should first check whether an existing table can credibly host this information without overloading its meaning. If not, introduce a minimal dedicated mechanism consistent with current DB conventions.

Minimal candidate shape for later implementation, following existing table patterns:

- run identifier
- pipeline name
- requested run date
- market trade date or source date loaded
- step name
- status
- inserted row count / updated row count when practical
- summary payload or error summary
- started at / completed at
- created at / updated at

This is a design direction, not a locked schema migration. The important contract is observability, not the exact table name.

### Decision: price display must carry explicit provenance

Current repo behavior widely uses `close_price` and `trade_date`, while dashboard payloads sometimes already carry `as_of_date`. Future implementation should make that provenance explicit instead of silently overloading one field.

Required distinctions:

- latest actual close: the latest market close from the latest valid market trade date the system can prove
- raw close: the persisted unadjusted market close used for normal display unless otherwise stated
- adjusted close: only for explicitly configured analytical or backtest use, never silently substituted into ordinary display
- trade date: the market date associated with the quoted price
- source date: the upstream payload date or requested market date when different from local display date
- created_at / ingestion timestamp: when the row or run state was stored locally, not a substitute for market trade date

Current repo reality matters here: most persisted recommendation and dashboard surfaces do not yet carry a distinct price-basis field. Future implementation should add explicit metadata alongside existing `close_price` surfaces rather than forcing a breaking rename.

### Decision: displayed stale or fallback prices must be visibly marked

If the UI or API shows a price from a previous trading day, fallback snapshot, or stale cache path, the response must say so through date and source metadata. The system must not silently present an older price as if it were today's fresh close.

Dashboard default behavior should prefer latest actual market close together with trade date. Recommendation display should identify the recommendation trade date and price basis. If backtest validation uses an adjusted basis later, that basis must be explicit in both persisted results and any surfaced summaries.

### Decision: latest price selection must order by market trade date, not ingestion timestamp alone

The correctness contract is that "latest" means latest valid market trade date first. Local insertion order or `created_at` may help diagnose data freshness, but it cannot be the primary selector for displayed latest market price.

This aligns with existing repo helpers such as `get_valid_market_dates()`, `get_actual_latest_date()`, and chart preparation logic that already centers `trade_date`.

### Decision: daily backtest validation must be lightweight and scheduler-safe

The repo already has `jobs/run_backtest.py` plus persisted `backtest_trades` and `backtest_equity_curve`. The daily scheduler should not run the full historical CLI workflow. Instead, future implementation should add an optional lightweight validation pass that is operationally bounded.

Design constraints:

- run after recommendation generation, never before
- operate on a bounded recent window such as 60, 120, or 250 trading days
- use a configured strategy subset and fixed validation universe
- finish fast enough for scheduled daily use
- persist status and summary separately from full research artifacts when necessary

The purpose is anomaly detection:

- stale or missing data
- broken strategy logic
- NaN propagation
- impossible returns
- price/date alignment issues

### Decision: failed validation must not silently corrupt recommendation persistence

`daily_recommendations` remains the canonical recommendation contract. Lightweight validation may fail and should be recorded as failed, but that must not retroactively corrupt or silently rewrite already-persisted recommendation rows.

Whether scheduler failure semantics stop the pipeline or continue with warnings can be decided during implementation, but the persisted run state must make that outcome explicit.

### Decision: full historical and optimization backtests remain outside the daily scheduler

`jobs/run_backtest.py` and `jobs/optimize_params.py` remain research or manual tooling surfaces. The daily scheduler will not absorb full history, parameter sweeps, or expensive portfolio exploration.

### Decision: cleanup is staged and evidence-based

No file deletion happens without verification. A future cleanup pass must gather evidence that a file or path has:

- no imports
- no CLI references
- no README or docs references
- no docker-compose references
- no tests depending on it
- no scheduler dependency
- no OpenSpec references
- no user-facing documented workflow

When a path still matters for compatibility, the implementation should first mark it deprecated, redirect docs to the official path, and add guardrail tests around the official path before removal.

## Risks And Trade-offs

- Adding run-state persistence increases operational clarity but adds another maintained persistence surface.
- Making price provenance explicit may reveal existing stale-data behaviors that were previously hidden by silent fallbacks.
- A lightweight daily validation pass improves safety, but even bounded validation adds scheduler time and failure modes that must be observable.
- Cleanup may take more than one implementation round because evidence-based deprecation is intentionally slower than direct deletion.

## Verification Strategy

- Validate that `jobs/scheduler.py` remains the single official daily scheduled entrypoint in docs, tests, and OpenSpec artifacts.
- Add contract tests for step observability, stale/fallback price visibility, and latest-price ordering by `trade_date`.
- Verify that recommendation persistence still centers `daily_recommendations`.
- Verify that optional daily validation runs after recommendation persistence and records status without pretending to be full research backtesting.
- Verify that cleanup candidates are covered by inventory evidence before any removal.
