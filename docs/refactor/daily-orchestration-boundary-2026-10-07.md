# Daily orchestration boundary evidence (2026-10-07)

## Stage map

`jobs/run_daily.py` currently owns one compatibility-sensitive orchestration:

1. resolve the trade date and runtime flags;
2. load market data and calculate indicators;
3. resolve active strategy identity through `StrategyManager`/Registry;
4. select and rank candidates through the Strategy Runner;
5. persist recommendation rows and the run heartbeat;
6. run the isolated Fundamental production/shadow adapter;
7. emit summaries and return status for the scheduler.

The stages share date resolution, fallback metadata, database persistence,
dry-run behavior, and exception handling. The daily characterization tests
assert recommendation identity/order/count, persistence fields, dry-run
behavior, Fundamental isolation, and scheduler ownership.

## Decision

**DEFER extraction.** No stage has a narrow input/output and side-effect
contract that can be extracted without introducing a generic pipeline or
changing rollback seams. Keep the concrete orchestration and document the
concentration as debt. A future extraction must compare trade date, stock ID,
direction, order, quantity, database fields, and Fundamental fallback with
`rtol=1e-9` and `atol=1e-12` where numeric values are involved.

## Evidence

```text
python -m pytest -q --timeout=10 test/test_cli_dry_run.py test/test_run_daily_persistence.py test/characterization/test_scheduler_baseline.py
```

The CLI/scheduler characterization subset passed. No runtime structural change
was made to `jobs/run_daily.py` in this phase.
