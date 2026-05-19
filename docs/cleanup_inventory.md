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
