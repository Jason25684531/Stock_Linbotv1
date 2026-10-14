# Deletion Candidates

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
