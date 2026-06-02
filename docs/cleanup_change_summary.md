# Cleanup Change Summary

Change: `final-cleanup-governance-audit`  
Date: 2026-06-02

## Modified Files

- `.gitignore`
  - Added `.coverage` so future coverage runtime output is ignored.
- `core/db_helper.py`
  - Replaced the only bare `except:` in `validate_setting_value` with `except (TypeError, ValueError):`.
- `openspec/changes/final-cleanup-governance-audit/tasks.md`
  - Marked implementation tasks completed as work progressed.

## Added Files

- `docs/cleanup_audit_report.md`
  - Evidence-backed audit of canonical architecture, compatibility wrappers, removed launchers, low-risk cleanup candidates, deferred consolidation, and baseline blockers.
- `docs/cleanup_change_summary.md`
  - This summary.
- `docs/cleanup_validation_report.md`
  - Baseline, post-change, focused test, smoke test, and residual-risk report.

## Removed Files

- `.coverage`
  - Removed tracked runtime artifact after confirming it was tracked and present at repo root.

## Duplicate Definition Consolidation

- No production duplicate helpers were consolidated in this pass.
- Financial supplementation duplication across daily, realtime/dashboard, and backtest paths was documented as a deferred tested consolidation candidate.
- Strategy hook methods such as `filter_candidates`, `name`, `features`, and `take_profit` were classified as intentional strategy polymorphism, not cleanup duplication.

## Deprecated Items Retained

- `tool/*`
  - Retained as deprecated compatibility wrappers to `core.*`.
- `app.py`
  - Retained as the legacy Flask app facade.
- `4_run_backtest.py`
  - Retained as compatibility launcher for `jobs.run_backtest`.
- `5_push_to_line.py`
  - Retained as compatibility launcher for `jobs.push_to_line`; `execution/morning_run.bat` still calls it directly.

## Removed Numeric Launchers

- `1_update_database.py`, `2_rundaily.py`, `3_train_model.py`, and `6_optimize_params.py` remain absent.
- They were not recreated.

## Unresolved Risks And Reasons

- `python -m compileall .` still fails inside `myenv/Lib/site-packages/aenum/_py2.py`, which is virtualenv package code with Python 2 syntax.
- `python -m pytest test -q` still has one existing failure in `test_openspec_change_artifacts_are_not_gitignored`, unrelated to this cleanup and tied to existing OpenSpec `.gitignore` policy.
- Historical docs such as `doc/UpdateList.md` still mention old launchers. They were left unchanged because archived/historical references are not active operator guidance by themselves.
- Financial supplementation duplication remains intentionally deferred because it needs field-level regression tests before consolidation.

## Test Results

- `python -m compileall .`
  - Failed due virtualenv package blocker: `myenv/Lib/site-packages/aenum/_py2.py`.
- `python -m pytest test -q`
  - 323 passed, 1 failed, 1 skipped.
  - Failure: existing OpenSpec gitignore-policy test for `consolidate-daily-data-backtest-pipeline`.
- `python -m pytest test/test_strategy_factory.py -q`
  - 3 passed.
- `python -m pytest test/test_richmenu_mcp_integration.py -q`
  - 49 passed.
- `python -m pytest test/test_mcp_integration.py -q`
  - 8 passed.
- Smoke checks:
  - `python -c "import app; ..."` passed.
  - `python jobs/scheduler.py evening --dry-run --stop-on-error` passed without real LINE push.
  - `python jobs/push_to_line.py --time evening --dry-run` passed with `would_push: false`.

