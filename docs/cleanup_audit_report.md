# Cleanup Audit Report

Change: `final-cleanup-governance-audit`  
Audit date: 2026-06-02  
Branch: `006-pipeline-progress-sync`

## Baseline

| Check | Result | Notes |
|---|---|---|
| `git status --short --branch` | Clean tracked worktree before cleanup | Branch `006-pipeline-progress-sync...origin/006-pipeline-progress-sync`; OpenSpec change directory is ignored by existing `.gitignore` policy. |
| `python -m compileall .` | Failed | First run timed out due Windows cp950 stdout encoding. UTF-8 rerun reached project files but failed on `myenv/Lib/site-packages/aenum/_py2.py`, a virtualenv Python 2 compatibility file. |
| `python -m pytest test -q` | 323 passed, 1 failed, 1 skipped | Failure: `test_openspec_change_artifacts_are_not_gitignored`, an existing OpenSpec `.gitignore` policy issue for `consolidate-daily-data-backtest-pipeline`. |
| `openspec status --change "final-cleanup-governance-audit"` | Complete | 4/4 artifacts complete. |
| `openspec validate "final-cleanup-governance-audit" --strict` | Valid | Change is apply-ready. |

## Findings

| 類型 | 檔案/函式 | 發現 | 風險 | 建議處理 | 是否自動修改 |
|---|---|---|---|---|---|
| Canonical architecture | `app/`, `jobs/`, `core/` | Active imports, Flask routes, scheduler wiring, tests, and README/project docs point to `app/`, `jobs/`, and `core/` as current runtime architecture. | Older prompts and archived docs may still describe `tool/` or numeric launchers as canonical. | Treat current working-tree evidence as source of truth. | No |
| Compatibility wrapper | `tool/*` | `tool/_proxy.py` aliases legacy `tool.*` modules to `core.*`; wrappers call `apply_module_proxy(...)`. One active test imports `tool.db_helper` to protect compatibility. | Deleting `tool/*` can break old import paths and compatibility tests. | Keep wrappers; mark/defer removal until all deletion gates pass. | No |
| Compatibility wrapper | `app.py`, `4_run_backtest.py`, `5_push_to_line.py` | Root facades remain present. `4_run_backtest.py` and `5_push_to_line.py` proxy to `jobs.*`; README documents them as remaining wrappers. | Removing wrappers may break operator commands and external launch habits. | Retain; do not delete in this cleanup. | No |
| Removed launcher status | `1_update_database.py`, `2_rundaily.py`, `3_train_model.py`, `6_optimize_params.py` | Files are absent. Active tests assert scheduler does not use `1_update_database.py` or `2_rundaily.py`; README says numeric wrappers were removed. | Recreating them would reverse prior cleanup and reintroduce duplicate entrypoints. | Do not recreate. Treat active references as blockers only if found in current operator workflows. | No |
| Active reference | `execution/morning_run.bat` | Morning batch still calls `5_push_to_line.py --time morning`. | This keeps `5_push_to_line.py` active as a user-facing compatibility launcher. | Retain `5_push_to_line.py`. | No |
| Historical reference | `doc/UpdateList.md`, `doc/V35_FINAL_VERIFICATION_2026-02-10.md`, archived OpenSpec | Many old references mention numeric launchers and `tool/*`. | False positives if historical notes are treated as active dependencies. | Leave historical archives unchanged; document mismatch only. | No |
| Runtime artifact | `.coverage` | `.coverage` exists at repo root, is tracked by git, and is a test runtime artifact. | Keeps stale runtime state in repo and can confuse coverage/test tooling. | Remove tracked `.coverage`; add `.coverage` to `.gitignore`. | Yes |
| Missing ignore rule | `.gitignore` | No `.coverage` ignore rule was found. | Future coverage runs can recreate an untracked artifact. | Add `.coverage` ignore rule. | Yes |
| Bare exception | `core/db_helper.py:1222` | One bare `except:` remains in `validate_setting_value`. | Bare handler catches system-exiting exceptions and violates cleanup policy. | Replace with explicit `except (TypeError, ValueError):` preserving behavior for invalid values. | Yes |
| Tested consolidation candidate | `jobs/run_daily.py`, `core/db_helper.py`, `jobs/run_backtest.py` | Financial supplementation for `revenue_yoy`, `op_profit_margin`, and `eps` is repeated across daily, realtime/dashboard, and backtest paths. | Consolidating without field-level tests could alter strategy inputs for V34/V35/V38. | Defer to a separate tested consolidation change. | No |
| Intentional polymorphism | `core/strategies/*` | Repeated `filter_candidates`, `name`, `display_name`, `features`, `target_return`, `stop_loss`, `take_profit`, and `max_hold_days` are strategy interface hooks. | Treating hook names as duplication could damage StrategyManager contracts. | Do not consolidate by name alone. | No |
| Transport boundary | `core/mcp_client.py`, `services/mcp/server.py`, crawlers | Covered dataset transport is centered on MCP client/server, with crawler boundaries still present for scraper-specific fetches. | Moving HTTP calls broadly would change architecture beyond low-risk cleanup. | No HTTP boundary changes in this pass. | No |
| Baseline blocker | `myenv/Lib/site-packages/aenum/_py2.py` | `compileall .` fails inside virtualenv package due Python 2 syntax. | Full-repo compileall remains noisy while `myenv/` is included. | Record as environment blocker; do not modify virtualenv. | No |
| Baseline blocker | `test/test_stabilize_daily_recommendation_pipeline_contract.py` | Full pytest has one existing failure about OpenSpec artifacts ignored by `.gitignore`. | This may remain after low-risk cleanup because `.gitignore` policy is broader than `.coverage`. | Record; focused tests still required after cleanup. | No |

## Deferred Risks

- `app/__init__.py`, `core/db_helper.py`, `core/mcp_client.py`, `core/line_message_builder.py`, and `jobs/run_backtest.py` are large files, but splitting them is outside this low-risk cleanup.
- Financial supplementation consolidation needs dedicated tests for daily, realtime/dashboard, and backtest data shapes.
- Archived OpenSpec and historical docs still mention old paths; these are not active blockers unless a current workflow references them.
