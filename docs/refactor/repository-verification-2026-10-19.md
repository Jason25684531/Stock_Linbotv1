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
