# Architecture simplification validation (2026-10-07)

## Completed

- Application submodules use direct imports and the minimal composition proxy;
  package-namespace service-locator references are gone from `app/*.py`.
- `create_app()` exists without removing the global Flask app, root launcher, or
  legacy CLI surfaces.
- README was rewritten in UTF-8 and now documents current ownership, data flow,
  Strategy Registry/Runner/Manager responsibilities, Fundamental isolation,
  manifests, compatibility, provenance, and verification.
- No source, dependency, strategy key, database record, or immutable research
  artifact was deleted or rewritten.

## Validation results

| Check | Result |
|---|---|
| `python -m compileall -q app core jobs` | PASS |
| Fresh `app` and five application submodule imports | PASS |
| Strategy/Backtest/Fundamental focused tests | 37 passed |
| CLI/legacy launcher/scheduler focused tests | 11 passed |
| Dashboard prewarm/stock snapshot/payload baseline | 13 passed |
| LINE/Web/recommendation/Fundamental compatibility | 30 passed |
| Strategy Platform/runtime contract tests | 51 passed |
| Daily/persistence/scheduler/platform tests | 20 passed; README contract suite 6 passed |
| `git diff --check` | PASS; only existing line-ending notices |
| OpenSpec strict validation | PASS |
| `docker compose config --quiet` with temporary validation env | PASS |

The full suite collected `898 items / 1 skipped` but was stopped by the
10-second timeout in a live-DB daily selection smoke. The dashboard macro
fallback test can likewise wait on an unavailable external news/MCP service.
These are environmental limitations, not reported as full-suite passes.

## Remaining debt

1. Replace the compatibility proxy with explicit typed collaborators only after
   Web/LINE tests no longer depend on patching the `app` module directly.
2. Split dashboard payload domains only after fixture-backed external-service
   equivalence evidence exists.
3. Keep `jobs/run_daily.py` concrete until one stage has a narrow side-effect
   contract and exact recommendation/persistence parity evidence.
4. Complete dynamic/non-Python dependency inventory before classifying any
   broad-freeze package as `SAFE_TO_REMOVE`.

## Ponytail review

- `app/__init__.py:148`: **yagni (deferred)** — `_ApplicationServices` is a
  dynamic one-implementation proxy. Replace it with explicit collaborators once
  compatibility tests stop patching `app` globals; deleting it now would break
  the established monkeypatch seam.

Net safe reduction in this phase: **0 additional lines**. The README already
removed the stale architecture bulk; no further runtime deletion has complete
compatibility evidence.
