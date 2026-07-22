# Cleanup inventory

## Final classification

Every entry records its active path, legacy compatibility status, and a decision:
**remove now**, **defer**, or **unknown / needs verification**. A removable
candidate requires evidence for imports, CLI references, README/docs references,
docker-compose references, scheduler references, test dependencies, OpenSpec
references, and user-facing workflow references.

OpenSpec references are recorded with the same deletion evidence as executable references.

| Path | Classification | Decision | Evidence / recovery |
|---|---|---|---|
| `jobs/scheduler.py` | active path | defer | scheduler references; fallback / recovery guidance: run the documented scheduler command |
| `execution/daily_run.bat` | legacy compatibility | defer | CLI references `jobs/scheduler.py`; deprecation marker retained |
| `1_update_database.py` | removable candidate | remove now | replaced by `jobs/update_database.py` |
| `jobs/run_daily_backtest_validation.py` | active path | defer | scheduler and test dependencies |

This inventory is evidence based; no unknown item is removed without a recorded decision.
