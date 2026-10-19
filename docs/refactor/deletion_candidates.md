# Deletion Candidates

## 2026-10-19 unreachable login cleanup

Baseline: e22d1a4, initially clean working tree. These entries authorize only
the named template, manifest lines and obsolete test exclusion.

| Candidate | Static, dynamic and compatibility evidence | Decision / replacement |
|---|---|---|
| `templates/login.html` (62 lines) | Hidden/no-ignore repository search for `login.html` finds historical audit hashes, archived migration prose/scripts and `test/test_web_ui_conventions.py`'s exemption only. No active renderer/include/extends/get_template/select_template references it. Every current `render_template` call was inspected; targets are literal supported dashboard/stock/strategy/system/backtest templates. `app/web_server.py:83-92` redirects GET/POST login and logout to dashboard. Existing `test_web_auth_safety.py` asserts these redirects. No dynamic template-name resolver or external public template API is present. Historical mentions remain evidence, not callers. | SAFE_TO_REMOVE; nothing replaces the unreachable form. Preserve login/logout routes. |
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
