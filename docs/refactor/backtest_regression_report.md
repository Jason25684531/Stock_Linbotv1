# Backtest regression report

Date: 2026-07-22

The Phase 2 V31 baseline covers 2026-04-01 through 2026-04-10. Regression compares dates, stock IDs, sides, sequence and quantities exactly. Numeric performance uses `rtol=1e-9` and `atol=1e-12`. The characterized loop now lives in `core/backtest/runner.py`; `jobs/run_backtest.py` is a compatibility CLI only.

Validation is recorded in the final Phase 10 run.

Targeted checks passed: metrics/validation/visualization unit tests (5), strategy
registry and legacy CLI characterization tests (2), and the compatibility CLI
smoke suite (68). `ruff` is not installed in this environment; syntax-critical
flake8 checks passed.

Full-suite run: 337 passed / 5 failed before follow-up compatibility fixes. The
wrapper, OpenSpec-ignore and V38 display failures were fixed and their focused
checks pass. The fixed-date V34 characterization count was refreshed from 549
to 543 after re-running the same 2026-04-10 database snapshot; its historical
legacy key is intentionally retained in the fixture. The user-deleted
`docs/cleanup_inventory.md` contract remains outside this change.
