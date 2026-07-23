# Deletion Candidates

> 此文件是證據閘門，不是刪除授權。除 CACHE 外，任何 `UNKNOWN` 或 `LEGACY_COMPATIBILITY` 檔案均不得在本 Phase 刪除。

| 路徑／模式 | 分類 | 引用證據 | 刪除理由 | 影響 | 回復方式 | 決定 |
|---|---|---|---|---|---|---|
| `__pycache__/` | CACHE | `.gitignore` 規則；Python bytecode 可重建 | 可重建快取 | 無原始碼影響 | 重新執行 Python | Phase 3 可刪 |
| `.pytest_cache/`、`.coverage` | CACHE | `.gitignore` 規則；pytest 可重建 | 可重建測試快取／覆蓋率輸出 | 無測試語意影響 | 重新執行 pytest | Phase 3 可刪 |
| `htmlcov/`、`.mypy_cache/`、`.ruff_cache/` | CACHE | tasks Phase 3.1 定義的工具產物 | 可重建報告／快取 | 無原始碼影響 | 重新執行工具 | Phase 3 可刪 |
| `tool/**` | LEGACY_COMPATIBILITY | `tool/_proxy.py` 與 29 個 facade；外部使用未知 | 非刪除候選 | 可能破壞舊 import | `git revert` | 保留 |
| `4_run_backtest.py`、`5_push_to_line.py` | LEGACY_COMPATIBILITY | app、bat、測試、文件引用 | 非刪除候選 | 破壞舊 CLI | `git revert` | 保留 |
| `specs/**`、`doc/**` | UNKNOWN | 歷史與維運文件；尚無遷移方案 | 證據不足 | 遺失歷史脈絡 | `git revert` | 保留 |
| `core/viz_helper.py`、`core/report_helper.py` | UNKNOWN | 仍有程式引用 | 未完成重疊分析 | 報表／圖表退化 | `git revert` | 保留 |
| `stop`、`docs/cleanup_*.md` | UNKNOWN | 已缺失且為既有工作樹變更 | 本 Change 不持有其刪除決策 | 可能影響既有工作 | 使用者原分支／revert | 保留現狀 |
