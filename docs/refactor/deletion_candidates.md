# Deletion Candidates

## 2026-11-02 zero-reference symbols and one-off scripts

Change: `openspec/changes/2026-11-02-architecture-hygiene-dead-code-audit`. Baseline `cd5b044`.
Working-tree snapshot (`outputs/refactor/hygiene_20261102/baseline_status.txt`): 6 modified and
69 untracked paths owned by the in-flight `dual-track-research-mode` change. Every path in that
snapshot is out of scope.

Search: `rg -n --hidden --no-ignore` over the whole repository, excluding `.git`, `myenv`, `data`,
`ML_Data`, `outputs`, `artifacts`, `__pycache__`, `.env*` and this change's own directory. The
search covers source, tests, templates, `execution/*.bat`, docker-compose files, notebooks,
README, docs, `specs/`, and active plus archived OpenSpec. Dynamic checks covered `getattr`,
`importlib`, string dispatch, `__all__` and the scheduler `JobSpec` table.

| Candidate | Evidence | Decision |
|---|---|---|
| `core/strategy_manager.py::get_strategy_manager` (7 lines) | Definition is the only hit. The module has no `__all__`. No string or `getattr` lookup. Sibling helpers `get_active_strategy` and `get_active_strategies` stay. | SAFE_TO_REMOVE. Replacement: `StrategyManager()`. |
| `core/db_helper.py::is_recommendation_heartbeat` (3 lines) | Definition is the only hit. Heartbeat filtering uses `include_heartbeats` and `RECOMMENDATION_HEARTBEAT_STOCK_ID`. | SAFE_TO_REMOVE. Replacement: the existing constant comparison. |
| `core/calc_indicators.py::add_all_indicators` (31 lines) | Definition is the only hit. Every indicator it calls has another importer: `calculate_bias` is imported by `jobs/run_daily.py`, and the rest have 3 to 5 call sites each. | SAFE_TO_REMOVE. Replacement: nothing. |
| `scripts/dual_track_closure.py` (181 lines) | The only hits are the file's own strings. Not wired into the scheduler, `execution/`, tests or docs. The closure outputs it produced remain on disk. | Owner decision 2026-11-02: REMOVE. Rollback: `git checkout cd5b044 -- scripts/dual_track_closure.py`. |
| `scripts/inspect_tquant_notebooks.py` (41 lines) | Zero hits outside the file. It was a one-off print tool for the 2026-10-19 TQuant field audit. | Owner decision 2026-11-02: REMOVE. Rollback: `git checkout cd5b044 -- scripts/inspect_tquant_notebooks.py`. |
| `core/validation/rolling.py::rolling_metrics`, `core/validation/param_scan.py::parameter_scan` | No callers. Hits named `rolling_metrics` in the research scripts are a dictionary key, not this function. Both are required by the `stability-validation` spec. | KEEP+TEST. Owner agreed 2026-11-02. |
| `scripts/research_mode_charts.py::write_factor_charts`, the three uncalled functions in `core/research/d5_research_variant.py` | Untracked files owned by `dual-track-research-mode`. `write_factor_charts` gained a caller between the two scans. | OUT_OF_SCOPE. |

Historical evidence still names the two scripts. The 2026-10-23 dual-track
`reproduction_manifest_closure.json` and `reproduction_manifest_final.json` list
`python scripts/dual_track_closure.py ...` as reproduction commands. Three research baselines
record both scripts in `tracked_sha256.json` or `preflight.json`. No source or test reads those
code hashes back. The manifest readers check only their `outputs` digests. Those records stay
unchanged as history. Reproducing the 2026-10-23 closure requires restoring the script from
`cd5b044` first.

No dependency, compatibility launcher, strategy alias, facade import or research evidence is
removed. Rollback: revert the single Phase 2 commit.

## 2026-10-19 unreachable login cleanup

Baseline: e22d1a4, initially clean working tree. These entries authorize only
the named template, manifest lines and obsolete test exclusion.

| Candidate | Static, dynamic and compatibility evidence | Decision / replacement |
|---|---|---|
| `templates/login.html` (57 lines) | Hidden/no-ignore repository search for `login.html` finds historical audit hashes, archived migration prose/scripts and `test/test_web_ui_conventions.py`'s exemption only. No active renderer/include/extends/get_template/select_template references it. Every current `render_template` call was inspected; targets are literal supported dashboard/stock/strategy/system/backtest templates. `app/web_server.py:83-92` redirects GET/POST login and logout to dashboard. Existing `test_web_auth_safety.py` asserts these redirects. No dynamic template-name resolver or external public template API is present. Historical mentions remain evidence, not callers. | SAFE_TO_REMOVE; nothing replaces the unreachable form. Preserve login/logout routes. |
| Flask-Login pins in `requirements.runtime.txt` and `requirements.txt` | Hidden/no-ignore search for flask_login/Flask-Login/login_required/LoginManager/UserMixin finds pins and historical documentation/spec/copilot instructions only. No source/test/tool import, importlib lookup, plugin registration or runtime authentication wiring. Installed distribution metadata lists zero reverse requirements for Flask-Login. `create_app()` returns the global app; login is already public redirect behavior. Delta spec corrects obsolete LoginManager expectation before cleanup. | SAFE_TO_REMOVE direct dependency lines; keep Flask and its dependencies. No environment-wide uninstall. |
| `test/test_web_ui_conventions.py:55-56` login exemption | Sole active filename mention skips checking the unreachable template. After removal the exemption has no target. | SAFE_TO_REMOVE code section; all remaining templates follow the same test. |

Search command pattern: `rg -n --hidden --no-ignore` excluding `.git`, `myenv`,
`data`, `ML_Data`, `outputs`, `artifacts`, `__pycache__`, and `.env*`. Included
source, tests, launchers, deployment, ignored docs/specs and active/archive
OpenSpec. Excluded datasets and generated evidence cannot register a template
renderer; installed package dependencies were checked separately. Reviewed
`render_template`, `get_template`, `select_template`, Jinja include/extends,
and current app composition. No supported external consumer is documented.

Rollback: revert only the 2026-10-19 cleanup commit to restore template, pins
and exemption. Validation: baseline/post-change offline suite, existing auth
redirect/UI contracts, live browser navigation and actual Web/MCP restart.
Research artifacts, source facades, legacy strategy aliases, root CLI scripts,
operator settings and database history are KEEP. Linter-only unused imports
are not deletion evidence, especially facade re-exports.

## 2026-10-16 runtime audit private helper evidence

The following private code sections are **SAFE_TO_REMOVE**, with replacement
**nothing**. This classification does not authorize deleting their source files.
Before the new proposal/audit documents were written, a hidden/no-ignore search
over source, tests, scripts, batch/deployment files, active/archived OpenSpec and
documents returned only the six definitions. Search excluded `.git`, `myenv`,
datasets, generated outputs/artifacts and `.env*`; none of those paths defines
an executable caller or registration for these helpers. New audit documents
mention the symbols as evidence, not runtime references.

| Private section | Original lines | References / export and dynamic checks | Decision |
|---|---|---|---|
| core/news_agent.py::_records_to_json | 272-273 | Only definition; no import, tool schema registration, string/getattr lookup or public facade export. Other JSON serialization is in active callers. | SAFE_TO_REMOVE |
| core/research/pipeline/orchestrator.py::_required_frame_columns | 185-186 | Only definition; pipeline package star re-export excludes private names; run() derives required columns directly from each factor spec. | SAFE_TO_REMOVE |
| core/runtime/fresh_oos_promotion.py::_selected_assets_hash | 271-273 | Only definition; explicit __all__ excludes it. Active target identity uses _selection_hash/_target_hash; do not modify those functions. | SAFE_TO_REMOVE |
| jobs/run_engine_diagnostics.py::_custom_metrics | 36-38 | Only definition; run() reads performance/return evidence directly. CLI dispatch calls main/run, never private helper lookup. | SAFE_TO_REMOVE |
| jobs/run_fundamental_pit_v3.py::_publication_cache_exists | 189-193 | Only definition; active _load_cached_publication and batch cache paths validate their own files. Publication keys/cache constants have other callers and remain. | SAFE_TO_REMOVE |
| test/test_stabilize_daily_recommendation_pipeline_contract.py::_check_ignore | 15-20 | Only definition, not a pytest test/fixture/hook; no collection plugin refers to it. subprocess remains needed by the actual git-contract test. | SAFE_TO_REMOVE |

No dependency, non-cache file, compatibility launcher, strategy alias, protocol
hook or research source is removed. In particular core/strategy/__init__.py's
__dir__ and test/utils Alpha sources are KEEP. Existing module-level facade
imports are not classified by pyflakes alone. Retain `publication_request_key`,
MOPS cache constants, json/hashlib, and subprocess because active code uses them.

Verification: research pipeline/publication/Fresh OOS/diagnostic/news tests,
compatibility/dynamic-export checks, deterministic selection and full offline
suite. Rollback: revert only the independently committed 2026-10-16 Phase 2;
never restore or overwrite operator settings/research evidence.

## 2026-10-14 private helper removal evidence

`core/strategy/spec.py::_freeze_mapping` is SAFE_TO_REMOVE as a private code
section (9 lines), not a file. Hidden repository searches across source,
tests, scripts, deployment/batch files and documents found only its definition
before this evidence/proposal was written. No import, string/getattr/importlib
lookup, registration or export references it. `spec.__all__` exports only
StrategySpec and StrategySpecValidationError. The core.strategy dynamic bridge
loads core/strategy.py for historical public V30/V31 symbols, never this helper.
The adapter's dynamic imports load registered strategy classes; they do not
resolve spec helper names. The function is not a supported external interface.
Replacement: nothing; StrategySpec continues calling `_freeze` directly and
retains mapping/type validation. No non-cache file is removed. Validation:
strategy contracts, immutable runtime contracts, dependency boundary, registry
parity and the green Phase 0 suite. Rollback: independently revert Phase 1.

The three direct SDK imports in app/line_bot.py are unused after the baseline
correctness fix. app owns the SDK imports and services delegates to those same
objects. Repository callers of callback/postback/diagnosis/command replies use
reply_message; the facade patch contract continues to resolve the app objects.
Replacement: existing services composition seam; SDK validation stays enabled.
No dependency or exported app compatibility symbol is deleted.


## 2026-10-13 market-ingestion simplification

`jobs/update_database.py::fetch_twse_data`, `fetch_tpex_data`, their private
`HEADERS` constant and `requests` import are SAFE_TO_REMOVE code sections.
Repository searches across source, tests, scripts and documents found only
their definitions; the daily and backfill entry points call
`fetch_market_data_with_mcp`, and official parsing belongs to
`services/mcp/server.py`. They are not CLI wrappers or strategy aliases.
Replacement is the already-used canonical MCP client. No file or dependency
is deleted. Rollback is the source-phase commit revert. Existing market
integrity, MCP-route, strategy-center and UI regression tests provide the gate.

## 2026-10-07 architecture-simplification review

- `app_pkg` and `sys.modules[__package__]` lookups are **SAFE_TO_REMOVE** and
  were replaced by direct imports plus the small application composition proxy.
  The compatibility seam is retained because existing Web/LINE/dashboard tests
  patch symbols on `app`.
- Dashboard and `jobs/run_daily.py` concentration are **DEFER**, not deletion
  candidates. Their shared payload, persistence, fallback, and exception
  contracts need isolated characterization before extraction.
- Heavyweight packages without complete multi-surface evidence are **UNKNOWN**
  and remain installed. Static no-import evidence alone is insufficient.

Detailed ownership evidence is in
[`dependency-ownership-inventory-2026-10-07.md`](dependency-ownership-inventory-2026-10-07.md).

## 2026-10-06 C4 integration review

No new source deletion candidate has complete no-reference evidence. The C4
cutover retains `StrategyManager`, `BaseStrategy`, all seven legacy strategy
implementations, alias modules, `core/strategy.py`, root CLI wrappers, and
existing runtime owners for compatibility and rollback.

## 2026-09-21 refresh

The current reference map is `docs/refactor/repository_hygiene_reference_map_2026-09-21.md`.  It found no source or non-cache candidate with complete no-reference evidence.  `4_run_backtest.py`, `5_push_to_line.py`, `app.py`, `config.py`, `init_settings.py`, `core/viz_helper.py`, `core/report_helper.py`, `jobs/run_candidate_redundancy.py`, `jobs/run_engine_diagnostics.py`, `scripts/diagnose_strategies.py`, and `tmp_probe/` are retained under their recorded classifications.  Only confirmed idle cache paths may be removed in this change.

> 此文件是證據閘門，不是刪除授權。除 CACHE 外，任何 `UNKNOWN` 或 `LEGACY_COMPATIBILITY` 檔案均不得在本 Phase 刪除。

| 路徑／模式 | 分類 | 引用證據 | 刪除理由 | 影響 | 回復方式 | 決定 |
|---|---|---|---|---|---|---|
| `__pycache__/` | CACHE | `.gitignore` 規則；Python bytecode 可重建 | 可重建快取 | 無原始碼影響 | 重新執行 Python | Phase 3 可刪 |
| `.pytest_cache/`、`.coverage` | CACHE | `.gitignore` 規則；pytest 可重建 | 可重建測試快取／覆蓋率輸出 | 無測試語意影響 | 重新執行 pytest | Phase 3 可刪 |
| `htmlcov/`、`.mypy_cache/`、`.ruff_cache/` | CACHE | tasks Phase 3.1 定義的工具產物 | 可重建報告／快取 | 無原始碼影響 | 重新執行工具 | Phase 3 可刪 |
| `tool/**` | REMOVED | 2026-07-23 inventory found one repository import (`test/test_recommendation_fallback.py`) and no dynamic imports; the test now imports `core.db_helper`. `tool/stock_sector_map.json` SHA-256 matched `core/stock_sector_map.json`. See `openspec/changes/2026-07-23-remove-tool-proxy-and-slim-app-init/evidence.md`. | Removed after full repository inventory | External consumers must use `core.*` | `git revert` | 2026-07-23 |
| `4_run_backtest.py`、`5_push_to_line.py` | LEGACY_COMPATIBILITY | app、bat、測試、文件引用 | 非刪除候選 | 破壞舊 CLI | `git revert` | 保留 |
| `specs/**`、`doc/**` | UNKNOWN | 歷史與維運文件；尚無遷移方案 | 證據不足 | 遺失歷史脈絡 | `git revert` | 保留 |
| `core/viz_helper.py`、`core/report_helper.py` | UNKNOWN | 仍有程式引用 | 未完成重疊分析 | 報表／圖表退化 | `git revert` | 保留 |
| `stop`、`docs/cleanup_*.md` | UNKNOWN | 已缺失且為既有工作樹變更 | 本 Change 不持有其刪除決策 | 可能影響既有工作 | 使用者原分支／revert | 保留現狀 |

## 2026-11-11 architecture-boundary-and-dead-code-cleanup-v1

Change：`openspec/changes/2026-11-11-architecture-boundary-and-dead-code-cleanup-v1`。基線 `5c156e7`。

**Owner 決定（最終）**：刪除。中斷的 apply 執行曾在本節寫入「保留既有 CLI、Web 路由及研究／驗收證據」；Owner 隨後在兩份互相矛盾的紀錄間明確選擇刪除。下表的 REMOVED 項依此執行；`init_settings.py` 依 design D9 保留。

文件中的歷史提及不是呼叫者：`docs/tquant-strategies-verification-2026-10-19.md:48` 提到 `scripts/verify_tquant_cli.py`，`docs/research/2026-11-10-engine-accounting-and-history-gaps-v1/REPORT_TABLES.md:1` 提到 `scripts/engine_fix_report.py`，`docs/refactor/architecture_cleanup_2026-10-14/baseline.json` 記錄兩個 wrapper 與 4 個 CLI 的 SHA，`outputs/dual_track_validation/2026-10-23/**/reproduction_manifest_*.json` 記錄 `scripts/test_isolation_record.py`。以上皆不由程式回讀（`git grep` 全庫查無讀取 `architecture_cleanup_2026-10-14` 或這些 manifest 的程式碼雜湊者），保留為歷史；要重現時以下列指令還原。

| 刪除項 | 證據 | 判定 | 還原 |
|---|---|---|---|
| `scripts/milestone_checkpoint.py`、`test_isolation_record.py`、`native7_monitor.ps1` | 原始碼、測試、`.claude/` 零引用；只在 `outputs/` 證據出現；不在 87 個身份釘選檔內 | REMOVED（D6-A） | `git checkout 5c156e7 -- <路徑>` |
| `scripts/engine_fix_evidence.py`、`engine_fix_report.py`、`engine_fix_smoke.py`、`engine_fix_state.py`、`hist_foundation_fetch.py`、`historical_review.py`、`verify_engine_fix_browser.py`、`verify_reference_identities.py` | 同上；`app/platform_routes.py:462` 的 `engine_fix_state()` 是同名的另一個函式，只讀輸出目錄 | REMOVED（D6-A2） | 同上 |
| `scripts/verify_tquant_cli.py` | 唯一用途是 `runpy` 執行 `4_run_backtest.py`，隨 wrapper 失去對象 | REMOVED（D6-A3） | 同上 |
| `jobs/run_candidate_redundancy.py`、`run_engine_diagnostics.py`、`run_fundamental_factor_validation.py`、`run_fundamental_pit_f2.py` | 無匯入者、無 JobSpec、無 bat、無測試；身份閉包內沒有任何 `jobs/` 檔 | REMOVED（D6-B） | 同上 |
| `4_run_backtest.py`、`5_push_to_line.py` | `app/`、`jobs/`、`execution/`、compose 無執行期呼叫；作業系統排程的兩個 bat 不經 wrapper；canonical 為 `jobs/run_backtest.py`、`jobs/push_to_line.py` | REMOVED（D6-C／D7）；契約測試改為斷言不存在 | 同上 |
| 10 條路由：`/api/summary`、`/api/backtest-result`、`/api/trades`、`/api/pk/battle`、`/api/user/trade`、`/api/news_sentiment`、`/api/market/institutional`、`/api/research/strategy-status`、`/api/lab/summary`、`/update_mode` | 五道閘門全部通過（下表）；`url_map` 差異恰為這 10 條；只測這些路由的 `test/test_web_api_smoke.py`、`test/test_web_research_status.py` 一併刪除 | REMOVED（D6-G／D8） | `git checkout 5c156e7 -- app/web_server.py app/platform_routes.py app/__init__.py` 與兩個測試檔 |
| 隨路由失去呼叫者的 `app/__init__.py::_load_backtest_summary_or_error`、`_build_summary_response`、`web_server.RESEARCH_SNAPSHOT_DIR`，以及 `jsonify`、`get_backtest_summary_from_db`、`V34_MODE_PRESETS`、`V35_MODE_PRESETS` 匯入 | 刪除後全庫無其他使用者；不在 `__all__`（前者已一併移出）；無測試經 `app`／`services` patch 它們（唯一 patch 者是已刪的 smoke 測試；`test_research_isolation.py` 對 `RESEARCH_SNAPSHOT_DIR` 的 patch 已移除，其讀取防護不變） | REMOVED | 同上 |
| `init_settings.py` | `user_settings` 唯一建表與種子執行者 | KEEP（D9） | — |
| `/api/performance`、`/api/dashboard/macro`、`/api/dashboard/health-check`、`/api/daily-signals` | 有 README 或主規格指名 | KEEP／SPEC | — |
| `jobs/operations/`、`jobs/research/` | 只含 `__pycache__`；刪除指令遭自動審查以政策擋下 | CACHE，未刪 | — |

以下 `jobs/update_database.py` 內部舊執行鏈的查核維持不變。查核指令：`rg -n -w --hidden --no-ignore` 搜尋四個符號，涵蓋原始碼、測試、排程、bat、Compose、`.claude`、文件及 OpenSpec；排除 `.git`、虛擬環境、資料與輸出目錄。另檢查 `getattr`、`importlib`、JobSpec 及命令文字；完整輸出在本機 `outputs/refactor/hygiene_20261111/function_references.txt`（該目錄受 Git 忽略）。研究身份探針使用專案自身三個身份來源與 11 個入口，2026-11-11 結果中此檔不在 87 個釘選檔案內。外部 Python 呼叫無法由本機掃描證明不存在，故限於此舊鏈，既有 CLI `main()` 與其參數行為保留。復原：以基線 `5c156e7` 的該檔對照後擷取需要的函式。

| 項目 | 查核結果 | 判定 |
|---|---|---|
| `_run_update` | 定義但無呼叫；CLI 指向 `main()`；文字引用僅歷史設計與本變更 | SAFE_TO_REMOVE |
| `run_price_update` | 只由 `_run_update` 呼叫；歷史文件及一則舊註解提及 | SAFE_TO_REMOVE，刪除舊註解 |
| `get_latest_date_from_db` | 只由 `run_price_update` 呼叫 | SAFE_TO_REMOVE |
| `print_summary_report` | 只由 `_run_update` 呼叫 | SAFE_TO_REMOVE |
| `get_earliest_incomplete_date` | `test/test_ingestion_source_contract.py` 有呼叫 | KEEP |
| `run_monthly_revenue_update`、`run_financial_update` | `services/market_data_update.py` 文件引用 | DEFER |
| `core/backtest/engine.py`、`core/backtest/__init__.py`、`config.py` | 身份探針釘選 | KEEP／IDENTITY_PINNED |
| `init_settings.py` | `user_settings` 唯一建表與種子執行者 | KEEP |
| 策略檔方法、scheduler daily 與 bat、requirements | 策略與公開相容面、使用狀態未完整證明 | KEEP（D6 E、F、H） |
| 四個 `jobs/run_*` 候選 | 無匯入者、JobSpec、bat 或測試；外部呼叫無法由掃描證明不存在 | REMOVED（D6 B，Owner 最終決定） |
| 兩個數字 wrapper、通過閘門的 10 條路由 | 無執行期呼叫者；Owner 最終決定刪除 | REMOVED（D6 C、G） |
| 12 個一次性研究／驗收腳本 | 只有文件與 outputs 的歷史提及，無程式回讀 | REMOVED（D6 A、A2、A3，Owner 最終決定） |
| 正確性問題 | 不在本次清理範圍 | DEFER（D6 I） |

路由五道閘門的重跑結果：搜尋路徑及 `url_for` 端點名稱，範圍含 templates、app、core、jobs、services、scripts、execution、Richmenu、README、主規格及測試，輸出保存在本機 `outputs/refactor/hygiene_20261111/route_references.txt`；路由所在兩個檔案均不在身份釘選清單。通過五道閘門的 10 條依 Owner 最終決定刪除；有 README 或主規格指名的 4 條保留。

| 路由 | 靜態查核 | 最終決定 |
|---|---|---|
| `/api/summary` | 無模板或其他程式路徑引用；`api_backtest_result` 呼叫其函式 | REMOVED |
| `/api/news_sentiment` | 未發現模板、其他程式或主規格路徑引用 | REMOVED |
| `/api/lab/summary` | 未發現模板、其他程式或主規格路徑引用 | REMOVED |
| `/update_mode` | 未發現模板、其他程式或主規格路徑引用 | REMOVED |
| `/api/trades` | 測試引用；未發現模板、其他程式或主規格路徑引用 | REMOVED |
| `/api/backtest-result` | 測試引用；呼叫 `api_summary` | REMOVED |
| `/api/pk/battle` | 測試引用；未發現模板、其他程式或主規格路徑引用 | REMOVED |
| `/api/user/trade` | 測試引用；未發現模板、其他程式或主規格路徑引用 | REMOVED |
| `/api/market/institutional` | 測試引用；README 泛指 `/api/market/*` | REMOVED |
| `/api/research/strategy-status` | 測試引用；未發現模板、其他程式或主規格路徑引用 | REMOVED |
| `/api/performance` | README 指名 | KEEP／SPEC |
| `/api/dashboard/macro` | 主規格指名 | KEEP／SPEC |
| `/api/dashboard/health-check` | README 與主規格指名 | KEEP／SPEC |
| `/api/daily-signals` | 排程、研究驗證腳本及主規格引用 | KEEP／SPEC |

舊表狀態補充：`tmp_probe/`、`tool/**`、`doc/**`、`stop` 的已不存在對象為 CLOSED，並非允許移除其他現存文件。2026-11-02 的 `write_factor_charts` 與 `core/research/d5_research_variant.py` 三個符號已有呼叫者，撤回候選（WITHDRAWN）；歷史行保留供稽核。
