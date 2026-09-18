# TWSE Pipeline Architecture Inventory

Date: 2026-09-18

This is an implementation baseline for `twse-pipeline-boundaries-and-hygiene`.
It records ownership and disposition; it does not authorize deletion.

## Current ownership

| Area | Current paths | Disposition |
|---|---|---|
| Application | `app/`, `app.py`, `templates/`, `Richmenu/` | ACTIVE; preserve Web/LINE behavior |
| Official data and transport | `core/research/sources/`, `core/mcp_client.py`, `services/mcp/`, `core/crawlers/` | ACTIVE; source/provenance boundary |
| Research transformations | `core/research/{market,adjustment,factors,selection,pipeline,artifacts}/` with legacy re-export modules | ACTIVE; bounded responsibility packages with compatibility imports |
| Fundamental PIT | `core/research/fundamentals/pit.py`, `core/research/fundamental_pit*.py`, `jobs/run_fundamental_pit_v3.py` | ACTIVE / immutable-evidence producer; legacy module retained as shim |
| Frozen runtime | `core/runtime/`, `jobs/*fundamental*shadow*.py` | ACTIVE; shadow-only and order-disabled |
| Backtest | `core/backtest/`, `jobs/run_backtest.py`, `4_run_backtest.py` | ACTIVE plus LEGACY_COMPATIBILITY wrapper |
| Scheduler and jobs | `jobs/scheduler.py`, `jobs/*.py`, `execution/` | ACTIVE; one scheduler owner |
| Tests | `test/characterization/`, `test/unit/`, `test/fixtures/` | ACTIVE verification surface |
| Configuration | `config/`, `config.py`, `.env.example`, `strategy_settings.json`, Compose files | ACTIVE; secrets must remain environment-provided |
| Specs and agent workflow | `openspec/`, `specs/`, `AGENTS.md`, `.codex/` | DEVELOPMENT_TOOLING; active OpenSpec changes are explicitly reviewable |

## Generated and data roots

| Root | Classification | Rule |
|---|---|---|
| `data/processed/` | IMMUTABLE_EVIDENCE | Retain manifests and hashes; never silently overwrite |
| `outputs/` | REPRODUCIBLE_OUTPUT | Ignore locally; retain run identity, inputs, command, and hashes |
| `artifacts/` | REVIEW_OR_CACHE | Separate review reports from raw/resumable cache |
| `ML_Data/` | UNKNOWN_GENERATED | Retain until owner and reproducibility are confirmed |
| `tmp_probe/` | UNKNOWN_TEMPORARY | Do not remove while active-run use is unverified |
| `__pycache__/`, `.pytest_cache/`, `.coverage` | CACHE | Disposable after active-run check |

## Compatibility and worktree constraints

- Preserve the current uncommitted changes in `core/strategy_manager.py`, `jobs/scheduler.py`, `strategy_settings.json`, `core/runtime/`, new Fundamental jobs, and related tests.
- Preserve `4_run_backtest.py`, `5_push_to_line.py`, old module imports, scheduler commands, Web/LINE entrypoints, and legacy strategy keys until separately approved removal evidence exists.
- Static module-name scans found references for all `core/research` and `core/runtime` modules. No source file is classified `UNREFERENCED` by this inventory.
- Compose resolves `requirements.mcp.txt` and `requirements.runtime.txt`; both
  were installed successfully during the 2026-09-18 smoke run.
