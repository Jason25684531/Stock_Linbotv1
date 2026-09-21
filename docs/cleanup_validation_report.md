# Cleanup Validation Report — 2026-09-21

## Passed

- `openspec validate repository-hygiene-and-safe-cleanup-2026-09-21 --strict`: valid.
- `git diff --check`: passed before each committed phase and at final validation.
- `pytest -q -p no:cacheprovider test/test_stabilize_daily_recommendation_pipeline_contract.py::test_openspec_change_artifacts_are_not_git_tracked`: 1 passed after the local-OpenSpec correction.
- Full suite command: 772 passed, 1 xfailed.

## Failed / deferred

The full `pytest -q -p no:cacheprovider` run collected 779 tests and ended with 6 failures.  They are not converted into a pass:

1. `test_daily_selection_matches_the_fixed_date_baseline` reads the live configured database for the fixed date `2026-04-10`.  Its current strategy counts differ from the frozen fixture (`v34_turbo` 548 vs 543, `v35_innovation` 466 vs 446, `v38_value_dividend` 120 vs 116).  No baseline or database data was modified.
2. The former OpenSpec tracking contract failure was fixed and its targeted re-run passed.
3. Four `test_fundamental_production.py` cases expected the current operation evidence to reach the OOS gate.  The live `runtime_validation_manifest.json` is currently `BLOCKED` for `STALE_DATA` and omits `HISTORICAL_SELECTION_DRIFT`; the production boundary therefore correctly returns `HISTORICAL_DRIFT` before OOS/feature-flag evaluation.  No runtime evidence was overwritten and no fail-closed guard was weakened.

## Required follow-up

System-wide green status requires an isolated, versioned database fixture for the daily-selection characterization test and a coherent frozen Fundamental operation evidence set for production-gate tests.  Those are separate data/evidence maintenance changes, not safe cleanup edits.
