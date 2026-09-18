# Fundamental Daily Operations

This runbook operates the frozen Fundamental strategy through the existing
Stock_Linbotv1 scheduler. It does not enable live production or submit broker
orders. The current expected state is `SHADOW_APPROVED` with
`FRESH_OOS_STATUS=INSUFFICIENT_DATA`, so official Fundamental recommendation
rows remain zero.

## A. Start services

1. Start Docker Desktop.
2. Ensure `.env` contains the required local values from `.env.example`,
   including `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`,
   `MYSQL_USER`, `DB_URL`, `ADMIN_PASSWORD`, and `FLASK_SECRET_KEY`.
3. Start the existing services:

```powershell
docker compose up -d db twse_mcp_server stock_bot
docker compose ps
```

The application and MCP health endpoints are `/health` on ports 1688 and 8080
respectively.

## B. Daily scheduler command

Use only the official scheduler:

```powershell
python jobs/scheduler.py daily --stop-on-error
```

The logical order is:

```text
update_database
  -> fundamental_shadow_operation (cache-only by default)
  -> run_daily
  -> daily_backtest_validation
  -> push_to_line --time evening
```

## C. Fundamental refresh path

The operation step uses the existing `jobs/operate_fundamental_shadow.py`
entrypoint. It delegates to the canonical PIT/runtime/OOS modules and does not
run a second research implementation.

```powershell
python jobs/operate_fundamental_shadow.py --current-run-date 2026-09-17
```

Use `--allow-network` only for an explicitly authorized refresh. The default is
cache-only and should report `NETWORK_FETCHES=0` when all evidence is cached.

## D. Shadow/OOS accumulation

The operation appends only post-cutoff observations to the authoritative
operation root:

```text
outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1/
```

Important artifacts include `FreshOOSLedger.csv`,
`FreshOOSAvailabilityReport.json`, `promotion_review.json`, and
`runtime_validation_manifest.json`.

## E. Production eligibility evaluation

`jobs/run_daily.py` evaluates the production boundary after the legacy strategy
loop. It reads the frozen fingerprint, PIT/runtime gates, promotion evidence,
Fresh OOS minima, and `ENABLE_FUNDAMENTAL_PRODUCTION`.

The default is fail-closed:

```text
ENABLE_FUNDAMENTAL_PRODUCTION=false
FRESH_OOS_STATUS=INSUFFICIENT_DATA
PRODUCTION_READY=NO
PRODUCTION_ELIGIBILITY=BLOCKED
BROKER_ORDER_SUBMISSION=DISABLED
```

The machine-readable status is written beside the operation evidence as
`fundamental_production_status.json` during a normal (non-dry-run) daily run.

## F. `run_daily` behavior

- V31/V33-V38 continue through their existing strategy classes and persistence.
- Fundamental uses only the canonical runtime adapter.
- A blocked gate records status and writes zero official Fundamental rows.
- An eligible rebalance maps rows to the existing `daily_recommendations`
  columns; no new table is created.
- A non-rebalance returns `NO_REBALANCE_ACTION` and does not retarget.

## G. Web / LINE behavior

Web and LINE continue to resolve `daily_recommendations` through the shared
`core.db_helper` reader. A Fundamental request with no official snapshot shows
an explicit empty/blocked response and never falls back to another strategy.
Eligible rows use the existing Web and LINE formatters.

## H. Expected blocked state today

```text
STRATEGY_REGISTERED=YES
PRODUCTION_INTEGRATION=PASS
FRESH_OOS_TRADING_DAYS=37
FRESH_OOS_MONTHS=3
FRESH_OOS_REBALANCES=1
FRESH_OOS_STATUS=INSUFFICIENT_DATA
PRODUCTION_PROMOTION_STATUS=SHADOW_APPROVED
PRODUCTION_READY=NO
PRODUCTION_ENABLE_FLAG=FALSE
PRODUCTION_ELIGIBILITY=BLOCKED
PRODUCTION_BLOCK_REASON=INSUFFICIENT_OOS
FUNDAMENTAL_DAILY_RECOMMENDATIONS_WRITTEN=0
LINE_FUNDAMENTAL_RECOMMENDATIONS=0
BROKER_ORDER_SUBMISSION=DISABLED
```

## I. Incident handling

1. Read `fundamental_production_status.json` and the latest runtime validation
   manifest.
2. Treat `FINGERPRINT_MISMATCH`, `PIT_*`, `STALE_DATA`, `LOW_FACTOR_COVERAGE`,
   `HISTORICAL_DRIFT`, `PARITY_FAILURE`, and
   `PROMOTION_EVIDENCE_INVALID` as blocking incidents.
3. Do not update the frozen strategy, fixture, OOS minima, or promotion verdict
   to make a run pass.
4. Keep legacy strategy operations running only if the scheduler is not invoked
   with `--stop-on-error` and the incident policy permits it.

## J. Rollback

Rollback is configuration and entrypoint scoped:

1. Keep `ENABLE_FUNDAMENTAL_PRODUCTION=false`.
2. Disable or revert the single `fundamental_shadow_operation` scheduler step if
   the operation itself is faulty.
3. Leave `daily_recommendations` legacy rows and all V31/V33-V38 behavior
   untouched; no database migration is required.
4. Preserve Shadow/OOS evidence for diagnosis and rerun cache-only validation
   before re-enabling the scheduler step.

## Verification commands

```powershell
python jobs/operate_fundamental_shadow.py --current-run-date 2026-09-17
python -m pytest test/unit/runtime/test_fundamental_production.py -q
python -m pytest test/unit/runtime/test_fundamental_production_integration.py -q
python -m pytest test/unit/runtime/test_fundamental_web_line_compatibility.py -q
python -m pytest test -q
openspec validate complete-fundamental-production-integration-and-operations-v1 --strict
```
