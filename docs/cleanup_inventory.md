# Cleanup Inventory

This inventory tracks daily-pipeline-related entrypoints, wrappers, and launch paths without deleting files. The official scheduled owner remains `jobs/scheduler.py`.

## Classification Legend

- `active path`: official or still-required runtime path
- `legacy compatibility`: retained wrapper or compatibility launcher
- `removable candidate`: appears redundant but still requires verification before removal
- `unknown / needs verification`: usage is not yet fully proven

## Evidence Fields

- `imports`
- `CLI references`
- `README/docs references`
- `docker-compose references`
- `scheduler references`
- `test dependencies`
- `OpenSpec references`
- `user-facing workflow references`

## Inventory

| Path | Classification | Deprecation marker | Imports | CLI references | README/docs references | docker-compose references | scheduler references | test dependencies | OpenSpec references | user-facing workflow references | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `jobs/scheduler.py` | active path | n/a | none required; owns subprocess dispatch | `python jobs/scheduler.py daily`, `morning`, `evening`, `push_evening` | README runtime alignment and topology notes | no direct compose command; compose app topology assumes this path | source of truth | `test/test_stabilize_daily_recommendation_pipeline_contract.py`, `test/test_daily_pipeline_consolidation_contract.py` | `stabilize-daily-recommendation-pipeline`, `consolidate-daily-data-backtest-pipeline` | official operator workflow | Only official scheduled daily entrypoint |
| `jobs/update_database.py` | active path | no | imported by `jobs/backfill_pipeline.py` | direct CLI and scheduler target | README canonical flow and troubleshooting notes | no direct compose reference | invoked by `daily` and `evening` pipelines | persistence and integration tests | current change specs/tasks | manual repair and canonical daily pipeline | Phase 1 instruments `pipeline_runs` here |
| `jobs/run_daily.py` | active path | no | imported by `jobs/backfill_pipeline.py` | direct CLI and scheduler target | README canonical flow and provenance notes | no direct compose reference | invoked by `daily` and `evening` pipelines | `test/test_run_daily_persistence.py` and recommendation tests | current change specs/tasks | manual repair and canonical daily pipeline | Persists recommendations to `daily_recommendations` |
| `jobs/run_daily_backtest_validation.py` | active path | no | invoked by scheduler | optional direct CLI and scheduler target | README Phase 3 runtime alignment | no direct compose reference | invoked after `run_daily` | `test/test_daily_backtest_validation.py` | backtest automation spec and tasks | optional operational validation | Lightweight only; not full research backtest |
| `jobs/push_to_line.py` | active path | no | none required beyond scheduler/manual use | direct CLI and scheduler target | README canonical flow | no direct compose reference | invoked by `daily`, `evening`, `morning`, `push_evening` | channel sync and Flex tests | current change specs/tasks | scheduled and manual Line push workflow | Phase 1 instruments `pipeline_runs` here |
| `execution/daily_run.bat` | legacy compatibility | yes | none | local batch launcher | README legacy compatibility notes | no | points to `jobs/scheduler.py daily` | not directly tested | cleanup governance spec scope | Windows operator convenience | Compatibility wrapper for the official scheduler |
| `execution/morning_run.bat` | legacy compatibility | yes | none | local batch launcher | README Windows run notes | no | currently calls `5_push_to_line.py --time morning` instead of scheduler | not directly tested | cleanup governance spec scope | Windows operator convenience | Behavior retained; docs redirect daily ownership to scheduler |
| `execution/evening_run.bat` | legacy compatibility | yes | none | local batch launcher | README Windows run notes | no | currently calls legacy numeric launchers | not directly tested | cleanup governance spec scope | Windows operator convenience | Behavior retained; do not remove before evidence review |
| `execution/run_manual.bat` | legacy compatibility | yes | none | local interactive launcher | not prominently documented before Phase 3 | no | menu targets scheduler for most actions | no dedicated guardrail tests yet | cleanup governance spec scope | local operator convenience | Compatibility-only menu wrapper; not an official scheduler peer |
| `execution/start_web.bat` | unknown / needs verification | no | none | local launcher | README startup notes | no | no scheduler dependency | no dedicated guardrail tests yet | cleanup governance spec scope | local web convenience | Not part of daily data pipeline; verify before any deprecation |
| `1_update_database.py` | legacy compatibility | yes | wrapper import to `jobs.update_database` | possible direct CLI use | README legacy compatibility notes | no | not owned by scheduler | `test/test_phase2_chip_data.py` imports it | cleanup governance spec scope | legacy user muscle memory | Keep until cleanup evidence passes |
| `2_rundaily.py` | legacy compatibility | yes | wrapper import to `jobs.run_daily` | possible direct CLI use | README legacy compatibility notes | no | not owned by scheduler | indirect test reliance possible; no dedicated guardrail test | cleanup governance spec scope | legacy user muscle memory | Keep until cleanup evidence passes |
| `3_train_model.py` | legacy compatibility | yes | wrapper import to `jobs.train_model` | possible direct CLI use | README legacy compatibility notes | no | not owned by scheduler | no dedicated test found in current sweep | cleanup governance spec scope | legacy user muscle memory | Outside official daily pipeline |
| `4_run_backtest.py` | legacy compatibility | yes | wrapper import to `jobs.run_backtest` | possible direct CLI use | README legacy compatibility notes | no | not owned by scheduler | backtest tests focus on canonical job module | cleanup governance spec scope | legacy user muscle memory | Manual research surface, not daily scheduler |
| `5_push_to_line.py` | legacy compatibility | yes | wrapper import to `jobs.push_to_line` | possible direct CLI use | README legacy compatibility notes | no | not owned by scheduler | indirect compatibility use through batch files | cleanup governance spec scope | legacy user muscle memory | Keep until cleanup evidence passes |
| `6_optimize_params.py` | legacy compatibility | yes | wrapper import to `jobs.optimize_params` | possible direct CLI use | README legacy compatibility notes | no | not owned by scheduler | no dedicated test found in current sweep | cleanup governance spec scope | legacy user muscle memory | Manual optimization surface, not daily scheduler |
| `jobs/backfill_pipeline.py` | unknown / needs verification | no | imports `jobs.update_database` and `jobs.run_daily` | manual backfill/repair CLI | README manual repair notes | no | not part of official `daily` path | `test/test_backfill_pipeline.py` | design references repair tooling | manual repair workflow | Keep out of official scheduler ownership |
| `jobs/run_backtest.py` | active path outside daily scheduler | no | standalone backtest module | direct CLI and dashboard backtest API support | README backtest sections | no | explicitly outside official daily scheduler | backtest-focused tests | backtest automation spec | manual research and dashboard performance APIs | Not a cleanup target in Phase 3 |
| `scripts/twse_mcp_server.py` | unknown / needs verification | no | launcher for MCP service | local CLI launcher | README marks it as a legacy launcher | compose uses `python scripts/twse_mcp_server.py` inside `twse_mcp_server` | no daily scheduler dependency | MCP integration tests cover canonical routes, not this launcher directly | cleanup governance spec scope | local MCP startup convenience | Needs separate verification because compose still references it |

## Phase 3 Notes

- No files are deleted in this phase.
- No legacy script behavior is removed in this phase.
- Documentation now redirects official daily operations to `jobs/scheduler.py`.
- Deprecation markers can be added to compatibility wrappers and batch launchers without changing runtime behavior.
- A path must not be marked removable unless the evidence above stays current and removal gates pass.

## Removal Gate

Before any later deletion, verify all of the following for the candidate path:

- no imports
- no CLI references
- no README/docs references
- no docker-compose references
- no scheduler dependency
- no test dependency
- no OpenSpec reference
- no user-facing workflow dependency

## Cleanup Deletion Review

### Final Classification

| Candidate path | Previous classification | Final classification | Evidence summary | Decision | Reason |
| --- | --- | --- | --- | --- | --- |
| `1_update_database.py` | legacy compatibility | removed | no active imports after `test/test_phase2_chip_data.py` moved to `jobs.update_database`; no active README/operator docs references after README, dashboard, and LINE guide updates; no compose or scheduler references | remove now | thin wrapper only; supported path is `jobs/update_database.py` and `jobs/scheduler.py` |
| `2_rundaily.py` | legacy compatibility | removed | no active imports; diagnostic and operator guidance updated to `jobs/run_daily.py`; no compose or scheduler references | remove now | thin wrapper only; supported path is `jobs/run_daily.py` and the official scheduler flow |
| `3_train_model.py` | legacy compatibility | removed | no active imports, compose references, scheduler references, or supported docs references remain | remove now | training remains available through `jobs/train_model.py`; daily scheduler contract unchanged |
| `4_run_backtest.py` | legacy compatibility | legacy compatibility | still imported by `app/__init__.py` for backtest surfaces and still referenced by active runtime docs | defer | active runtime dependency remains |
| `5_push_to_line.py` | legacy compatibility | legacy compatibility | still used by `execution/morning_run.bat` and `execution/evening_run.bat`; still part of active operator guidance | defer | Windows compatibility wrappers still depend on it |
| `6_optimize_params.py` | legacy compatibility | removed | no active imports, compose references, scheduler references, or supported docs references remain | remove now | optimization remains available through `jobs/optimize_params.py`; outside official daily path |
| `execution/daily_run.bat` | legacy compatibility | legacy compatibility | still documented for Windows operators; wraps `jobs/scheduler.py daily` without introducing a second scheduler path | defer | compatibility wrapper remains useful |
| `execution/morning_run.bat` | legacy compatibility | legacy compatibility | still used for Windows morning push workflow and still calls `5_push_to_line.py` | defer | operator convenience path remains |
| `execution/evening_run.bat` | legacy compatibility | legacy compatibility | still used for Windows evening workflow and still references compatibility push flow | defer | needs a separate cleanup pass if wrapper behavior changes |
| `execution/run_manual.bat` | legacy compatibility | legacy compatibility | still provides a local operator menu; no equivalent replacement document yet | defer | user-facing workflow still exists |

### Decision Summary

- `remove now`: `1_update_database.py`, `2_rundaily.py`, `3_train_model.py`, `6_optimize_params.py`
- `defer`: `4_run_backtest.py`, `5_push_to_line.py`, `execution/daily_run.bat`, `execution/morning_run.bat`, `execution/evening_run.bat`, `execution/run_manual.bat`

### Fallback / Recovery Guidance

- If a removed numeric wrapper is unexpectedly still needed, restore it from version control and keep it as a thin `import_module('jobs.<name>')` compatibility shim while the missing workflow is documented.
- For operator recovery, prefer the supported commands first: `python jobs/scheduler.py daily`, `python jobs/update_database.py`, `python jobs/run_daily.py`, `python jobs/train_model.py`, and `python jobs/optimize_params.py`.
- Do not recreate a removed wrapper unless fresh evidence shows a real supported workflow still needs it.
