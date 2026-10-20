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
Phase results and requirement mapping follow below.

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

## Phase 3 runtime verification (2026-10-20)

- Cleanup commit: d64f72a. `execution/restart_services.bat --docker` exited 0 and confirmed both `/health` endpoints. PowerShell rendered Docker's stderr progress as NativeCommandError records in the redirected log; these were progress messages, not a failing command. The final command exit code and health results were successful.
- Restarted MCP StartedAt: **2026-10-19T22:49:42.154110556Z**; Web: **2026-10-19T22:49:42.119199658Z** (2026-10-20 in Asia/Taipei). Both changed from the baseline. MySQL remains running/healthy with StartedAt 2026-10-14T07:26:37.318485542Z; the supported restart command intentionally leaves DB running.
- `SELECT 1` succeeded from the restarted application container using its configured database connection. Both health endpoints returned HTTP 200/status ok; strategy, system-status and margin read APIs returned HTTP 200.
- Actual Chromium checked `/dashboard`, `/strategies`, `/stock-analysis?id=2330`, `/backtest` and `/system`: all HTTP 200, rendered main content and expected page titles. Strategy filtering, selection and apply-confirmation cancellation worked. Desktop 1440px and mobile 390px views passed; no horizontal overflow, uncaught page errors or failed requests. Screenshots were visually inspected. Evidence: `live_check.py`, `live-check.json`, `strategies-desktop.png`, `strategies-mobile.png` under the audit output directory.
- GET/POST `/login` and GET `/logout` returned 302 to `/dashboard`. No settings were applied and no LINE message was sent. Operator settings hash still matches the original capture.
- **Existing operational limitation:** system status is degraded because the stored 2026-10-19 `update_database` run failed with `Institutional data is unavailable for 2026-10-19; market batch not written`. Margin API correctly reports partial missing balance/previous-observation/zero-denominator data as degraded. The app remains available and displays those conditions. This cleanup does not claim the daily data pipeline is fully healthy or repair historical input data.
- Four expected failures retain historic strategy-listing and dashboard-layout characterization; eight integration tests remain deselected. Neither group is reported as passed.
- `.gitignore` acquired an unrelated uncommitted edit after the implementation commits. It is preserved and excluded from this change's commits.

## OpenSpec verification and ponytail review

Reviewed proposal, design, both delta specs and tasks using the spec-driven schema. Date-prefix rejection prevents CLI workflow status/apply instructions, so artifact loading and task accounting were performed directly; strict validation succeeds. No completeness, correctness or coherence dimension was skipped.

| Dimension | Assessment |
|---|---|
| Completeness | All implementation/runtime tasks complete; administrative sync/archive is the remaining final step. Five requirements and nine scenarios have evidence below. |
| Correctness | 5/5 changed requirements covered; 9/9 scenarios covered by automated or recorded acceptance checks. |
| Coherence | Bounded removals match deletion evidence; stdlib JSON redirection and existing fixtures preserve project patterns; factory specification matches observed compatibility behavior. |

| Requirement / scenario | Implementation and validation evidence |
|---|---|
| Reliable verification boundaries: warning during JSON diagnostics | `jobs/strategy_ops.py:35`; `test/test_strategy_diagnostics.py:80` asserts one JSON document on stdout and dependency warning on stderr; real subprocess JSON check passes. |
| Reliable verification boundaries: offline/unconfigured MySQL | `test/characterization/test_atomic_replace_table.py:15` integration marker and explicit database URL guard before table operations; focused run skips all three without configuration, broad offline run deselects them. |
| Evidence-gated cleanup: obsolete login asset | Precommitted `deletion_candidates.md:3`; removed template and manifest pins; existing auth redirects and common template tests pass, and live GET/POST redirects were checked. |
| Evidence-gated cleanup: uncertain artifact retained | `repository-audit-2026-10-19.md` KEEP decisions, actual diff retains aliases, facade entrypoints, research artifacts and operator configuration. |
| Architecture and debt evidence: whole repository assessment | Audit records inheritance/dispatch/encapsulation/system boundaries, five ponytail markers including batch/multiline forms, one missing trigger, benchmark disclosure and explicit architecture limitations. |
| Deployment verification: services restarted | Actual batch exit 0, changed StartedAt values, DB SELECT, both health endpoints, page/API/browser results and limitations recorded above. |
| Application factory: testable compatibility instance | Fresh-process check `create_app() is app.app`, registered `/callback`, no Flask-Login import; Web/LINE acceptance and regression tests pass. |
| Application factory: configured settings preserved | SHA-256 of strategy_settings.json captured before implementation and matched after suite/import/restart/browser checks; no settings changes in diff. |
| Application factory: legacy login links | `test/test_web_auth_safety.py:4` and live GET/POST login/GET logout redirect checks. |

CRITICAL: no missing implementation or scenario evidence. Administrative sync/archive remains to be completed before final task accounting.

WARNING: the live data-pipeline and margin degradation above remains an operational follow-up. Diagnose the named failed `update_database` run and its upstream institutional-data source before rerunning ingestion; use the existing backfill workflow for historical gaps. This does not block archiving the completed cleanup.

SUGGESTION: future work on shared application state, concentrated modules and container image startup remains in the audit; it is not reported as implemented here.

Ponytail-review of the code diff from e22d1a4 through d64f72a: **Lean already. Ship.** No additional complexity finding in this change. The stdlib context managers and one small JSON regression are sufficient; no new runtime abstraction or dependency was introduced.
