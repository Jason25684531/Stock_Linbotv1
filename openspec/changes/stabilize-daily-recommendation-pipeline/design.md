## Context

This repository already uses a canonical batch-and-read pipeline:

`jobs/update_database.py` -> `jobs/run_daily.py` -> `daily_recommendations` -> `/api/daily-signals` and Line push

The main design task is not to invent a new runtime, but to stabilize the contract around the runtime that already exists. The biggest mismatch is that the code, compose file, and docs do not yet agree on the app database identity, while scheduler ownership and healthcheck semantics are easy to misread during future maintenance.

## Goals

- Keep the real repo topology as the only source of truth
- Make scheduler ownership explicit
- Preserve `daily_recommendations` as the persisted recommendation contract
- Keep `MODEL_PATH` singular and stable
- Move app-facing `DB_URL` defaults and examples to non-root
- Keep compose readiness checks low-dependency and predictable

## Non-Goals

- Building a new production topology
- Introducing structured DB env parsing beyond `DB_URL`
- Adding broker or simulation runtime behavior
- Changing recommendation semantics already covered by prior recommendation fallback work

## Decisions

### Decision: `jobs/scheduler.py` is the official scheduled entrypoint

The repository already centralizes scheduled jobs in `jobs/scheduler.py`. This change makes that ownership explicit in both code-adjacent docs and OpenSpec.

The daily recommendation pipeline remains:

`jobs/update_database.py` -> `jobs/run_daily.py` -> `daily_recommendations` -> `/api/daily-signals` and Line push

No second scheduler path should be introduced.

### Decision: `daily_recommendations` remains the persisted recommendation contract

`jobs/run_daily.py` continues to produce persisted recommendation state, including heartbeat rows for zero-candidate runs. `/api/daily-signals` and Line push remain downstream readers of that persisted contract rather than a separate recomputation path.

### Decision: `MODEL_PATH` remains the single public model-path variable

The system already exposes `MODEL_PATH`. This change keeps that as the only public model-path knob and preserves the default:

`ML_Data/pkl/stock_ai_model.pkl`

The intent is to avoid a second competing env var or a container-only rewrite such as `/app/data`.

### Decision: `DB_URL` remains the public DB config surface

Application runtime continues to resolve DB access through `DB_URL`. This change does not introduce a new parser for `DB_HOST`, `DB_USER`, `DB_PASSWORD`, or `DB_NAME`.

The explicit change is that app defaults, compose app examples, and documentation move to a non-root DSN. Database initialization credentials in compose remain separate from the application connection contract.

### Decision: `/health` stays the container readiness endpoint

Compose healthchecks continue to probe `/health` using Python stdlib `urllib.request`.

Important boundary:

- `/health` is the container readiness endpoint
- `/api/dashboard/health-check` 是 dashboard payload API，不是容器 health endpoint
- `/api/dashboard/health-check` is a dashboard payload API, not the container health endpoint

This distinction must stay explicit in docs and verification so dashboard payload regressions are not mistaken for container readiness failures.

## Risks And Trade-offs

- Moving app defaults to a non-root DSN may expose environments that were implicitly relying on root-only setup.
- Keeping `DB_URL` as the only public DB contract is simpler, but it means connection details remain bundled in one string rather than structured env vars.
- This design intentionally avoids broader runtime refactors, so future production topology work will still need a separate change.

## Verification Strategy

- Add targeted contract tests for config, compose, docs, and OpenSpec artifacts
- Keep existing recommendation persistence and channel-sync tests green
- Verify that compose still uses Python stdlib `/health` checks
- Verify that docs, examples, and code defaults agree on `DB_URL` and `MODEL_PATH`
