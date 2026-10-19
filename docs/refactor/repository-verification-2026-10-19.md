# Repository verification: started 2026-10-19, continued 2026-10-20

Change: `2026-10-19-audit-repository-maintainability`.

## Phase 0 baseline

- Initial revision e22d1a4, clean working tree; operator strategy settings match HEAD.
- Python: project myenv 3.11.9. `pip check`: no broken requirements.
- Initial memory-SQLite attempt aborted on timeout because the application's queue-pool options are incompatible with SQLite's memory pool. This is not a passing test result.
- File-backed isolated SQLite run: **1045 passed, 23 failed, 3 errors, 5 deselected, 4 xfailed**, 425 warnings in 136.16s. Command: `python -m pytest -q -o addopts= --timeout=30 -m "not integration" --junitxml=outputs/repository_audit_2026-10-19/baseline-isolated.xml`; DB_URL points only at an audit-output SQLite file.
- Failures mapped to uncontrolled market inputs in 19 adapter cases, one LINE recommendation test, two UI market-date tests, and one real JSON stdout pollution defect. Three unmarked MySQL-only tests use unsupported SQLite DDL. No application cleanup had occurred at this point.
- Proposal/design/specs/tasks updated before affected implementation. Correct the isolation/JSON issues in a separate phase and re-establish green regression evidence.
- OpenSpec strict validation passed. Workflow `new change`/`status` are incompatible with date prefixes; artifacts follow the installed official schema directly.
- Initial Docker states: all running/healthy. StartedAt: DB 2026-10-14T07:26:37.318485542Z, MCP 2026-10-19T06:49:22.129579841Z, Web 2026-10-19T12:34:02.738259793Z. Startup LINE auto-sync is disabled.

Raw local evidence is retained in `outputs/repository_audit_2026-10-19/`.
Subsequent phase results and final requirement mapping will be appended after execution.

## Phase 1 reliability baseline (2026-10-20)

- Focused reproduced failures: **57 passed, 3 skipped** in 17.53s. Skips are the explicitly opt-in MySQL atomic-table tests.
- Full isolated offline suite after boundary fixes: **1069 passed, 8 deselected, 4 xfailed**, 425 existing warnings in 132.77s. No unexpected failures/errors. Same command/environment as above, report `reliability.xml`/`reliability.log`.
- The 8 deselected tests are 3 MySQL atomic-table cases, 2 live strategy-factory cases, live daily selection, live seeded trades and the opt-in cleanup database integration test. They are not claimed as passed. Frozen strategy/trade characterization remains in the passing suite.
- CLI `--json` now directs dependency warnings to stderr using stdlib context managers. A regression exercises warning preservation and JSON parsing. Human output remains unchanged.
- Strategy/LINE/UI test inputs now control their DB boundaries; expected strategy selection/order values were not altered.
- Integration-marker coverage, 356-file AST parse, compatibility CLI help (backtest/LINE/scheduler), settings hash and diff checks passed.
- OpenSpec proposal/evidence phase commit: d52a7f1. OpenSpec artifacts remain local per repository ignore policy.

## Phase 2 cleanup (2026-10-20)

- Reliability phase commit: 63f55f2.
- Removed only `templates/login.html`, its UI-test exclusion and Flask-Login direct pins from the two manifests, following the precommitted deletion evidence.
- `requirements.txt` is UTF-16LE with BOM. Verified byte-for-byte that only the encoded dependency line was removed, retaining the existing encoding and line endings.
- Post-cleanup Web/auth/UI/LINE/CLI/strategy/restart/environment checks: **147 passed** in 5.04s (`post-cleanup.xml`/`post-cleanup.log`). Full offline suite had passed immediately before this bounded template/manifest cleanup; it was not unnecessarily repeated.
- Fresh-process factory returns the compatibility app, `/callback` is registered and `flask_login` is not imported. Operator settings remain unchanged.
- `git diff --check` and OpenSpec strict validation passed.
