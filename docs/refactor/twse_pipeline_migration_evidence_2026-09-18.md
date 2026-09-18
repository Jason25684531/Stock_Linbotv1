# TWSE Pipeline Migration Evidence — 2026-09-18

## Responsibility moves

- `core/research/market/data.py` — canonical market contracts; `market_data.py`
  remains a re-export.
- `core/research/adjustment/normalization.py` — canonical Adjustment boundary;
  `normalize.py` remains a re-export.
- `core/research/factors/definitions.py` — canonical factor definitions;
  `core.research.factors` remains import-compatible.
- `core/research/selection/` — universe and target generation; old module
  imports remain valid.
- `core/research/fundamentals/pit.py` — PIT fundamentals; the historical
  module re-exports every public symbol, including legacy parser constants.
- `core/research/pipeline/orchestrator.py` and
  `core/research/artifacts/serialization.py` — canonical orchestration and
  serialization boundaries.
- `jobs/runtime/` — the single grouped runtime adapter currently needed;
  `jobs/scheduler.py` remains the only scheduler owner.

## Verification

- Research: 322 passed, 411 warnings.
- Runtime/backtest: 38 passed.
- Stage/artifact contracts: 5 passed.
- Scheduler, Compose, and legacy CLI contracts: 29 passed.
- Characterization: 28 passed, 1 skipped, 1 xfailed, 1 existing fixture
  mismatch. The mismatch is documented in the baseline; `jobs/run_daily.py`
  was not changed.
- Compose build/start: all three services healthy; MCP and application health
  endpoints returned HTTP 200.
- `git diff --check`: passed (only platform line-ending warnings).
- OpenSpec strict validation: passed.

No source, immutable evidence, output, unknown material, or user-owned
uncommitted file was deleted. A commit was intentionally not created because
the worktree contains pre-existing user changes; this evidence file and the
OpenSpec task checklist provide the reversible hand-off record.
