# Strategy Platform 1.0 Stabilization Evidence

## Baseline scope

Captured before any stabilization cleanup or delegation change on 2026-10-06.
The worktree already contained user-owned C1-C4 implementation/test changes;
none of those files were reset or rewritten by this change.

| Item | Evidence |
|---|---|
| Python | 3.11.9 (`C:\Users\Norlan\AppData\Local\Programs\Python\Python311\python.exe`) |
| OS | Windows 11 Professional, NT 10.0.26200 |
| DB availability | No repository `.db`, `.sqlite`, or `.sqlite3` file discovered; live DB smoke is unavailable in this environment and is not an expected-value oracle. Tests use isolated/temp DB fixtures where applicable. |
| Settings version | `3.0` |
| Settings timestamp | `2026-09-21T13:10:26.930441` |
| Active strategy | `quality_value_low_volatility` |
| Random pool | `v35_innovation`, `v36_chip_momentum`, `v38_value_dividend` |
| Per-strategy overrides | `{}` |
| Backtest defaults | `initial_capital=1000000`, `period_months=12` |
| Deterministic fixtures | `test/fixtures/baseline/daily_selection_2026-04-10.json`; `test/fixtures/baseline/v31_2026-04-01_2026-04-10.json`; `test/fixtures/baseline/fundamental_production_evidence_2026-09-17.json`; characterization tests under `test/characterization/` |

## Exact baseline commands and results

Focused C1-C4 baseline:

```text
python -m pytest -q -ra test/unit/strategy test/unit/recommendation test/unit/runtime test/unit/backtest test/characterization/test_strategy_listing_baseline.py test/characterization/test_strategy_platform_identity_baseline.py test/characterization/test_strategy_registry_regression.py test/characterization/test_legacy_strategy_selection_deterministic.py test/characterization/test_daily_selection_deterministic.py test/characterization/test_daily_selection_regression.py test/characterization/test_backtest_seed.py test/characterization/test_trade_sequence_regression.py test/characterization/test_legacy_backtest_result.py test/characterization/test_legacy_cli.py test/characterization/test_line_command_baseline.py test/characterization/test_scheduler_baseline.py test/characterization/test_web_backtest_persistence_baseline.py test/test_c4_rollback_characterization.py test/test_daily_platform_parity.py test/test_web_platform_parity.py test/test_cli_dry_run.py test/test_run_daily_persistence.py test/test_recommendation_fallback.py test/test_richmenu_mcp_integration.py
```

Result: `277 passed, 1 xfailed, 5 warnings, 0 failed`.

Full repository baseline:

```text
python -m pytest -q -ra
```

Result: `896 passed, 1 skipped, 1 xfailed, 423 warnings, 0 failed`.
The one skip is the optional `optuna` test; the xfail is the retained
pre-change strategy-listing characterization. Neither was reclassified.

## C4 closure gate

- C1 archive: `openspec/changes/archive/2026-10-05-strategy-platform-contract-2026-10-05/`
- C2 archive: `openspec/changes/archive/2026-10-06-legacy-strategy-platform-adapter-2026-10-05/`
- C3 archive: `openspec/changes/archive/2026-10-06-strategy-platform-runtime-contract-2026-10-06/`
- C4 archive: `openspec/changes/archive/2026-10-06-strategy-platform-integration-2026-10-06/`
- C4 tasks: `32/32` complete.
- C4 main specification: `openspec validate strategy-platform-integration --strict` PASS.
- C4 Web fail-closed evidence: `test/test_web_platform_parity.py::test_daily_signals_platform_error_is_explicit_and_does_not_use_different_strategy` asserts HTTP `500`, empty signals, original error, and no strategy substitution. The C4 archived requirement preserves HTTP status and forbids silent platform-to-different-legacy fallback.
- C4 rollback evidence: `test/test_c4_rollback_characterization.py` passed in the focused and full baseline suites.
- `strategy_settings.json`: no git semantic diff; values are recorded above.
- `git diff --check`: no whitespace errors; Git emitted only existing LF/CRLF conversion notices.
- Broker boundary: `BROKER_ORDER_SUBMISSION=DISABLED`; no broker submit/activation path was introduced by this change.

## Baseline decision

The baseline is green and is the immutable pre-cleanup reference. Daily and
Backtest parity have not drifted, Fundamental runtime tests preserve identity,
fingerprint, gates, OOS/promotion, eligibility, reason-code, and broker safety
contracts, and no cleanup group has yet been selected.

## Caller stabilization evidence

### Daily

`jobs/run_daily.py` obtains the compatibility manager's lazy runner at the job
boundary, passes legacy selection through `StrategyRunner.execute` and the
registered `LegacyStrategyAdapter`, and keeps a direct `filter_candidates`
branch only as the explicit rollback seam when no runner is supplied. The
Fundamental path is independent: `run_fundamental_production` calls
`FundamentalRuntimeAdapter.evaluate_production` and does not enter the seven-item
legacy selection path. Existing loading, indicators, model/news enrichment,
heartbeat, transaction, and persistence owners were not changed in this phase.

`test/test_daily_platform_parity.py`, `test/test_run_daily_persistence.py`, and
the Daily characterization tests passed. They cover strategy identity,
candidate count/IDs/order, recommendation and legacy persistence fields,
heartbeat/no-candidate behavior, dry-run, and isolated DB persistence behavior.
No repository live DB was available for a production DB smoke; the isolated/temp
DB tests passed and live availability is retained as an environment debt, not a
deterministic oracle.

### Backtest

`core/backtest/runner.py` obtains selection through the manager's runner and
`select_backtest_candidates`; the direct strategy call is retained only as the
rollback seam. Backtest continues to own signal/execution timing, exits, price,
cost, slippage, holdings, trade state, accounting, metrics, reports, and DB/CSV
persistence. `test/unit/backtest/test_runner_selection.py`, the seeded and trade
sequence characterization tests, and persistence/market-data guards passed.

### Web, LINE, CLI, scheduler, Fundamental

- Web route smoke passed for listing, canonical/alias daily signals, empty and
  invalid states, explicit platform error HTTP 500, and payload compatibility.
- LINE picker/postback, aliases, random pool, text/Flex/no-result behavior and
  Fundamental status tests passed.
- `4_run_backtest.py`, `5_push_to_line.py`, `jobs/run_backtest.py`, flags,
  aliases, defaults, and exit behavior passed their characterization/CLI tests.
- `jobs/scheduler.py` remains the sole scheduler owner; scheduler baseline and
  normal/dry-run/error flow tests passed.
- Fundamental adapter/identity/runtime/production integration tests passed with
  no lifecycle mutation, no eligibility-to-activation conversion, no order path,
  and `BROKER_ORDER_SUBMISSION=DISABLED`.

Focused route/smoke command result: `109 passed, 7 warnings, 0 failed`.

## Final verification

After adding the test-only extensibility proof:

- Focused platform/caller/extensibility suite: `265 passed, 7 warnings, 0 failed`.
- Full repository: `897 passed, 1 skipped, 1 xfailed, 423 warnings, 0 failed`.
- Blocked tests: `0`.
- `openspec validate strategy-platform-stabilization-cleanup-2026-10-06 --strict`: PASS.
- `git diff --check`: PASS; only existing Git LF/CRLF conversion notices were emitted.
- `strategy_settings.json`: semantic diff NONE; active strategy, random pool,
  overrides, and backtest defaults match the baseline. A test-generated
  `last_updated` timestamp was restored before final status.
- Broker assertion: `core.runtime.fundamental_production.BROKER_ORDER_SUBMISSION`
  is exactly `DISABLED`.
- Rollback characterization: `2 passed`; no schema/data migration hook is
  present in the rollback seam.

## Final recommendation

No source cleanup was safe to perform. Files removed: `NONE`. The change is
functionally verified and is `READY_TO_ARCHIVE`; this implementation session
does not archive it automatically.
