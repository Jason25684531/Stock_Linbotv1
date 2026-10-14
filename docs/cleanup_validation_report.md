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

## 2026-10-14 Runtime architecture acceptance

Exact pytest commands: [commands.json](refactor/architecture_cleanup_2026-10-14/commands.json). Environment/checksums: [baseline.json](refactor/architecture_cleanup_2026-10-14/baseline.json).

| Check | Result |
|---|---|
| Original LINE baseline after prerequisite fix | 126 passed, 1 warning, 20.61s |
| Broader precleanup baseline | 283 passed, 2296 warnings, 121.38s |
| Phase 1 targeted tests | 202 passed, 2 warnings, 39.35s |
| Phase 2 caller/golden tests | 102 passed, 1 warning, 42.55s |
| Final relevant regression | 365 passed, 2298 warnings, 126.96s |
| Disposable MySQL 8.0 and Flask health | 5 passed, 1.82s |
| Independent rollback probes | 19 passed per Phase; shared checklist reconciliation only |
| Final import-only LINE/health smoke | 2 passed, 0.82s |
| Existing Web/MCP HTTP health | Both 200, status ok |
| Existing app/MCP/MySQL containers | All healthy; no restart |
| Settings, unrelated tracked files, frozen artifacts, fingerprint and production gate | Unchanged; broker submission DISABLED |

The isolated MySQL container had no production bind mounts/database/volume; the child pytest process alone received its DB_URL/CLEANUP_TEST_DB_URL. Tests verified duplicate-write replacement, preservation of another strategy, heartbeat, dry-run and real MySQL atomic replace/failure recovery. The labelled container and anonymous test volume were removed. The existing Daily live-DB characterization used dry_run=True with skipped_persistence=True; it is a read/preview smoke, not a frozen numerical oracle.

Warnings are retained: legacy XGBoost model serialization; pandas stack/groupby.apply/downcasting/monthly frequency. No global suppression or package/model upgrade is added.

The global OpenSpec tracking test is **not green**: 11 initial tracked/deleted old artifacts remain in the user's pending changes. [repository_policy_check.json](refactor/architecture_cleanup_2026-10-14/repository_policy_check.json) proves those paths existed before this task and this change adds no remaining tracked OpenSpec paths. This prevents a claim that the entire repository test suite is green. The current change's runtime checks pass; no complete unrelated/full-repository suite was run. Production processes were not redeployed; the live health probe demonstrates availability, while local tests exercise the changed source.

OpenSpec strict/diff/encoding checks and the task/spec/design verification are recorded in the final report. Earlier 2026-09-21 results above are historical and are not overwritten by this acceptance record.
