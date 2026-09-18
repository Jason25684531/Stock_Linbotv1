# TWSE Pipeline Baseline — 2026-09-18

This is the pre-migration characterization record plus the post-migration
rerun for `twse-pipeline-boundaries-and-hygiene`.

## Results

| Command | Result |
|---|---|
| `.\myenv\Scripts\python -m pytest test\unit\research -q` | PASS — 319 passed, 411 warnings, 32.24s |
| `.\myenv\Scripts\python -m pytest test\unit\runtime test\unit\backtest -q` | PASS — 38 passed, 18.64s |
| `.\myenv\Scripts\python -m pytest test\characterization -q` (Docker/MySQL available) | 28 passed, 1 skipped, 1 xfailed, 1 failed |

The previous run was blocked because MySQL was unavailable. After Docker was
started, all database connection, atomic-table, and trade-sequence checks
passed. One fixed-date daily-selection comparison still differs from the
checked-in fixture: `v34_turbo` is 550 vs 543 and `v35_innovation` is 451 vs
446. `jobs/run_daily.py` is unchanged in this change, so this is recorded as
an existing database/fixture drift requiring owner review; no strategy formula
or fixture was changed.
