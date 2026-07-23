## Why

The current repository already has the core daily recommendation path in place, but several operational contracts are still split across code defaults, compose settings, and docs.

Today the app runtime still defaults to a root-backed `DB_URL`, the compose app service also connects as root, and the non-root application contract is only implicit. At the same time, `MODEL_PATH`, scheduler ownership, and `/health` readiness expectations are described in multiple places with different levels of precision.

This change stabilizes the real repo topology without inventing a production stack that does not exist here. It keeps the current pipeline shape and clarifies the runtime contract so future implementation or automation work can rely on one documented source of truth.

## What Changes

- Lock `jobs/scheduler.py` as the official scheduled entrypoint for the daily recommendation pipeline.
- Preserve the canonical path:
  `jobs/update_database.py` -> `jobs/run_daily.py` -> `daily_recommendations` -> `/api/daily-signals` and Line push.
- Keep `MODEL_PATH` as the single public model-path environment variable with default `ML_Data/pkl/stock_ai_model.pkl`.
- Change app-facing `DB_URL` defaults and examples to a non-root DSN while keeping database initialization credentials separate.
- Keep compose readiness checks pointed at `/health` using Python stdlib.
- Add an OpenSpec change package with fixed delta spec paths for scheduler pipeline, runtime config, database config, and container health.

## Scope

### In Scope

- Official daily scheduler path and recommendation persistence completeness
- Unified `MODEL_PATH` handling with the existing `ML_Data` default
- Explicit non-root DB connection contract using `DB_URL`
- Compose healthcheck and runtime documentation consistency

### Out Of Scope

- Broker-backed simulation execution
- New broker abstractions
- `prod.docker-compose.yml`
- `web/Dockerfile`
- `DailyScreener`
- `MockBroker`
- `AlpacaBroker`
- Production-only topology that does not exist in this repo

## Impact

- Daily recommendation reliability is easier to reason about because scheduler ownership and the persistence path are explicit.
- `/api/daily-signals` remains centered on `daily_recommendations` as the persisted recommendation contract.
- Line push behavior remains aligned with the same persisted recommendation flow.
- Runtime configuration is clearer because `MODEL_PATH` and `DB_URL` each keep one public contract.
- Database privilege expectations are clearer because app runtime examples stop normalizing root access.
- Docker compose healthchecks stay compatible with slim Python images by continuing to use stdlib `/health` probes.
